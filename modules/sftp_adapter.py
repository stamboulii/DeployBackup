
import os
import stat
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Suppress noisy paramiko transport/auth logging
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("paramiko.transport").setLevel(logging.WARNING)

try:
    import paramiko
except ImportError as e:
    logger.warning(f"Failed to import paramiko: {e}")
    paramiko = None
except Exception as e:
    logger.warning(f"Unexpected error importing paramiko: {e}")
    paramiko = None

class SFTPAdapter:
    """
    Adapter to make paramiko.SFTPClient look like ftplib.FTP.
    This allows re-using existing FTP-based logic with SFTP.
    """
    def __init__(self, timeout=300):
        self.timeout = timeout
        self.ssh = None
        self.sftp = None
        self.host = None
        self.port = 22
        self.user = None
        self.password = None
        self.welcome = "220 SFTP Ready"

    def _log(self, msg, level=logging.DEBUG):
        logger.log(level, f"[SFTP] {msg}")

    def dir(self, *args):
        """
        Mimics FTP.dir
        """
        cmd = 'LIST'
        callback = None
        if len(args) == 1:
            if callable(args[0]):
                callback = args[0]
            else:
                cmd = f'LIST {args[0]}'
        elif len(args) == 2:
            cmd = f'LIST {args[0]}'
            callback = args[1]
            
        return self.retrlines(cmd, callback)

    def connect(self, host, port=22):
        self.host = host
        self.port = port
        return self.welcome

    def login(self, user, password):
        if not paramiko:
            raise ImportError("Paramiko is required for SFTP support. Please install it: pip install paramiko")
            
        self.user = user
        self.password = password
        
        # Retry logic for connection (DNS/Network glitches)
        import time
        import random
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(self.host, port=self.port, username=self.user, password=self.password, timeout=self.timeout)
                self.sftp = self.ssh.open_sftp()
                
                cwd = self.sftp.getcwd()
                self._log(f"SFTP Connected. Initial CWD: {cwd}", level=logging.INFO)
                return "230 Login successful"
                
            except Exception as e:
                self._log(f"Connection attempt {attempt+1}/{max_retries} failed: {e}", level=logging.WARNING)
                if attempt < max_retries - 1:
                    sleep_time = (attempt + 1) * 2 + random.uniform(0, 1)
                    time.sleep(sleep_time)
                else:
                    raise ConnectionError(f"Failed to connect to SFTP {self.host} after {max_retries} attempts: {e}")

    def cwd(self, path):
        try:
            self.sftp.chdir(path)
            self._log(f"Changed directory to: {path}", level=logging.INFO)
            return "250 Directory successfully changed"
        except IOError as e:
            self._log(f"Failed to CWD to {path}: {e}", level=logging.ERROR)
            raise Exception(f"550 Failed to change directory to {path}")

    def pwd(self):
        return self.sftp.getcwd()

    def mkd(self, path):
        try:
            self.sftp.mkdir(path)
            return path
        except IOError:
            raise Exception("550 Create directory operation failed")

    def delete(self, path):
        try:
            self.sftp.remove(path)
            return "250 Delete operation successful"
        except IOError:
            raise Exception("550 Delete operation failed")
            
    def rmd(self, path):
        try:
            self.sftp.rmdir(path)
            return "250 Remove directory operation successful"
        except IOError:
            raise Exception("550 Remove directory operation failed")

    def voidcmd(self, cmd):
        """Dummy implementation for keepalives/NOOP"""
        if cmd.upper() == 'NOOP':
            try:
                # Just check if channel is active
                if self.ssh and self.ssh.get_transport() and self.ssh.get_transport().is_active():
                    return "200 NOOP ok"
                else:
                    raise Exception("Connection lost")
            except:
                raise Exception("Connection lost")
        return "200 Command okay"

    def retrbinary(self, cmd, callback, blocksize=262144):
        """
        Mimics FTP.retrbinary
        cmd format: "RETR filename"
        """
        filename = cmd.replace('RETR ', '').strip()

        # Normalize path
        filename = filename.replace('/./', '/')
        if filename.startswith('./') and len(filename) > 2:
            filename = filename[2:]

        # Helper to try download with a specific path
        def try_download(path):
            try:
                with self.sftp.open(path, 'rb') as f:
                    f.set_pipelined(True)
                    f.prefetch()
                    while True:
                        data = f.read(blocksize)
                        if not data: break
                        callback(data)
                return True
            except IOError:
                return False

        # 1. Try exact path
        if try_download(filename):
            return "226 Transfer complete"
            
        # 2. Try relative path (strip leading /)
        if filename.startswith('/') and try_download(filename.lstrip('/')):
            return "226 Transfer complete"

        self._log(f"SFTP open failed for {filename}. Trying SCP/CAT fallback...", level=logging.WARNING)
        
        # Fallback: CAT via SSH
        try:
            # Helper for cat
            def try_cat(path):
                stdin, stdout, stderr = self.ssh.exec_command(f'cat "{path}"')
                # Check stderr only after reading? No, assume stream works.
                # If cat fails, it might write to stderr and close stdout
                
                # Check if we got any data
                got_data = False
                while True:
                    data = stdout.read(blocksize)
                    if not data: break
                    callback(data)
                    got_data = True
                    
                err = stderr.read().decode('utf-8')
                if err and not got_data:
                    raise IOError(err)
                return True

            try:
                if try_cat(filename): return "226 Transfer complete (fallback)"
            except:
                # Try relative
                if filename.startswith('/'):
                    if try_cat(filename.lstrip('/')): return "226 Transfer complete (fallback)"
                    
            raise IOError("CAT failed")

        except Exception as e2:
            self._log(f"CAT fallback failed: {e2}", level=logging.ERROR)
            raise Exception(f"550 Failed to open file {filename}: {e2}")

    def download_file(self, remote_path, local_path, blocksize=262144):
        """
        Direct SFTP file download with prefetch for maximum throughput.

        Uses ``prefetch()`` to pipeline read requests over the SSH channel,
        hiding network latency.  blocksize defaults to 256 KB which is
        optimal for most networks (vs Paramiko's internal 32 KB default).
        """
        # Normalize path
        remote_path = remote_path.replace('/./', '/')
        if remote_path.startswith('./') and len(remote_path) > 2:
            remote_path = remote_path[2:]

        def _download(path):
            with self.sftp.open(path, 'rb') as rf:
                rf.set_pipelined(True)
                rf.prefetch()
                with open(local_path, 'wb') as lf:
                    while True:
                        chunk = rf.read(blocksize)
                        if not chunk:
                            break
                        lf.write(chunk)
            return True

        # Try exact path first
        try:
            return _download(remote_path)
        except IOError:
            pass

        # Try without leading slash
        if remote_path.startswith('/'):
            try:
                return _download(remote_path.lstrip('/'))
            except IOError:
                pass

        raise IOError(f"Failed to download {remote_path}")

    def storbinary(self, cmd, fp, blocksize=8192):
        """
        Mimics FTP.storbinary
        cmd format: "STOR filename"
        """
        filename = cmd.replace('STOR ', '').strip()
        try:
            self.sftp.putfo(fp, filename)
            return "226 Transfer complete"
        # Since putfo handles reading from fp, we don't manually loop with blocksize here
        # but paramiko does it internally.
        except IOError as e:
            raise Exception(f"550 Failed to upload file {filename}: {e}")

    def _list_files(self, path):
        """
        Robust listing that tries multiple strategies
        Returns list of SFTPAttributes or objects with same interface
        """
        # Normalize path
        if path == '/.': path = '.'
        path = path.replace('/./', '/')
        
        paths_to_try = [path]
        if path.startswith('/'):
            paths_to_try.append(path.lstrip('/'))
            
        for p in paths_to_try:
            # Strategy 1: listdir_attr with provided path
            try:
                return self.sftp.listdir_attr(p)
            except IOError:
                pass
                
            # Strategy 2: listdir_attr with empty string (sometimes required for current dir)
            if p == '.' or p == './' or p == '':
                try:
                    self._log("Fallback: trying listdir_attr('')")
                    return self.sftp.listdir_attr('')
                except IOError:
                    pass

            # Strategy 3: listdir + stat
            files = []
            try:
                self._log(f"Fallback: trying listdir({p}) + stat")
                names = self.sftp.listdir(p)
                for name in names:
                    try:
                        full_path = name if p in ('.', './', '') else f"{p}/{name}"
                        attr = self.sftp.stat(full_path)
                        attr.filename = name
                        files.append(attr)
                    except IOError:
                        from paramiko.sftp_attr import SFTPAttributes
                        attr = SFTPAttributes()
                        attr.filename = name
                        attr.st_size = 0
                        attr.st_mtime = time.time()
                        attr.st_mode = 0o100644 
                        files.append(attr)
                if files: return files # Only return if we found something? Or return empty list if successful?
                # If listdir succeeded but empty, return empty
                return files
            except IOError:
                pass

            # Strategy 4: SSH exec_command (Hail Mary)
            try:
                cmd = f"ls -la {p}" if p not in ('.', './', '') else "ls -la"
                self._log(f"Fallback: trying SSH exec_command('{cmd}')")
                stdin, stdout, stderr = self.ssh.exec_command(cmd)
                
                out = stdout.read().decode('utf-8')
                err = stderr.read().decode('utf-8')
                
                if out:
                    files = []
                    from paramiko.sftp_attr import SFTPAttributes
                    
                    for line in out.splitlines():
                        parts = line.split()
                        if len(parts) < 9: continue 
                        
                        name = " ".join(parts[8:])
                        if name in ('.', '..'): continue
                        
                        attr = SFTPAttributes()
                        attr.filename = name
                        try: attr.st_size = int(parts[4])
                        except: attr.st_size = 0
                        attr.st_mtime = time.time() 
                        
                        perms = parts[0]
                        mode = 0
                        if perms.startswith('d'): mode |= stat.S_IFDIR
                        else: mode |= stat.S_IFREG
                        attr.st_mode = mode
                        files.append(attr)
                    return files
            except Exception as e:
                self._log(f"SSH strategy failed for {p}: {e}")
        
        raise IOError("All listing strategies failed.")

    def retrlines(self, cmd, callback=None):
        """
        Mimics FTP.retrlines, specifically for MLSD and LIST
        """
        command = cmd.split()[0].upper()
        path = cmd[len(command):].strip() or '.'
        
        lines = []
        
        # Simplify path
        if path == '.' or path == './':
            path = '.'
        elif path.endswith('/.'):
            path = path[:-2]
        
        if command == 'MLSD':
            try:
                self._log(f"MLSD scanning path: {path}")
                attrs = self._list_files(path)
                
                for attr in attrs:
                    if attr.filename in ('.', '..'):
                        continue

                    # Détecter les symlinks
                    is_symlink = stat.S_ISLNK(attr.st_mode)
                    is_dir = stat.S_ISDIR(attr.st_mode)
                    
                    if is_symlink:
                        self._log(f"Skipping symlink: {attr.filename}", level=logging.DEBUG)
                        continue
                        
                    entry_type = 'dir' if is_dir else 'file'
                    size = attr.st_size
                    # MLSD format for time: YYYYMMDDHHMMSS
                    mtime = datetime.fromtimestamp(attr.st_mtime).strftime('%Y%m%d%H%M%S')
                    
                    line = f"type={entry_type};size={size};modify={mtime};unix.mode={attr.st_mode}; {attr.filename}"
                    lines.append(line)
                    
            except IOError as e:
                self._log(f"MLSD error on {path}: {e}", level=logging.ERROR)
                raise Exception(f"MLSD failed: {e}")
                
        elif command == 'LIST' or command == 'DIR':
            try:
                self._log(f"LIST scanning path: {path}")
                attrs = self._list_files(path)
                
                for attr in attrs:
                    if attr.filename in ('.', '..'):
                        continue
                        
                    # Détecter les symlinks
                    is_symlink = stat.S_ISLNK(attr.st_mode)
                    is_dir = stat.S_ISDIR(attr.st_mode)
                    
                    if is_symlink:
                        self._log(f"Skipping symlink: {attr.filename}", level=logging.DEBUG)
                        continue
                    
                    type_char = 'd' if is_dir else '-'
                    perm_str = type_char + 'rwxrwxrwx' # Fake perms
                    size = str(attr.st_size)
                    name = attr.filename
                    
                    line = f"{perm_str} 1 ftp ftp {size} Jan 01 00:00 {name}"
                    lines.append(line)
            except IOError as e:
                self._log(f"LIST error on {path}: {e}", level=logging.ERROR)
                pass
        
        elif command == 'NLST':
            try:
                lines = self.sftp.listdir(path)
            except IOError:
                pass
                
        else:
            raise NotImplementedError(f"Command {command} not implemented in SFTPAdapter")

        if callback:
            for line in lines:
                callback(line)
        
        return "226 Transfer complete"

    def sendcmd(self, cmd):
        """
        Handle MDTM and other simple commands
        """
        if cmd.startswith('MDTM'):
            path = cmd[5:].strip()
            try:
                attr = self.sftp.stat(path)
                return "213 " + datetime.fromtimestamp(attr.st_mtime).strftime('%Y%m%d%H%M%S')
            except IOError:
                raise Exception("550 Failed to get file info")
        
        if cmd == 'TYPE I':
            return "200 Type set to I"
            
        raise NotImplementedError(f"Command {cmd} not implemented in SFTPAdapter")

    def quit(self):
        self.close()
        return "221 Goodbye"

    def close(self):
        if self.sftp:
            try:
                self.sftp.close()
            except: 
                pass
            self.sftp = None
        
        if self.ssh:
            try:
                self.ssh.close()
            except:
                pass
            self.ssh = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
