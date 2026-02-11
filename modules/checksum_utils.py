"""
Checksum utilities for file verification
More reliable than size comparison
"""
import hashlib
import os
import logging

logger = logging.getLogger(__name__)


def calculate_file_hash(file_path: str, algorithm: str = 'md5', chunk_size: int = 65536) -> str:
    """Calculate file hash without loading entire file in memory"""
    hash_func = hashlib.new(algorithm)
    try:
        with open(file_path, 'rb') as f:
            while chunk := f.read(chunk_size):
                hash_func.update(chunk)
        return hash_func.hexdigest()
    except Exception as e:
        logger.warning(f"Failed to calculate hash for {file_path}: {e}")
        return None


def calculate_remote_hash(ssh, remote_path: str, algorithm: str = 'md5') -> str:
    """
    Calculate hash on remote server using ssh if available.
    This avoids downloading the file just to verify it.
    
    Args:
        ssh: paramiko SSHClient
        remote_path: Path to file on remote server
        algorithm: Hash algorithm (md5, sha1, sha256)
    
    Returns:
        Hash string if successful, None if failed
    """
    try:
        # Try native hash command (most servers have md5sum or sha256sum)
        hash_cmd = f'{algorithm}sum'
        
        # Check which hash command is available
        for cmd in [f'{algorithm}sum', f'{algorithm}']:
            stdin, stdout, stderr = ssh.exec_command(f'command -v {cmd}', timeout=10)
            if stdout.read().strip():
                hash_cmd = cmd
                break
        
        stdin, stdout, stderr = ssh.exec_command(
            f'{hash_cmd} "{remote_path}"', timeout=60
        )
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        
        # Parse output (format varies: "hash  file" or "hash\r\nhash")
        if out:
            parts = out.split()
            if len(parts) >= 2:
                return parts[0]
            elif len(parts) == 1 and len(parts[0]) in [32, 40, 64]:  # MD5, SHA1, SHA256
                return parts[0]
        
        # Try Python hash as fallback (slower, requires transfer)
        if 'command not found' in err.lower() or not out:
            logger.debug(f"No {algorithm}sum on server, will use size comparison")
            return None
            
    except Exception as e:
        logger.debug(f"Remote hash calculation failed: {e}")
        return None
    
    return None


def verify_download_integrity(ssh, sftp, local_path: str, remote_path: str, 
                             expected_hash: str = None, expected_size: int = None,
                             algorithm: str = 'md5') -> tuple:
    """
    Verify downloaded file integrity using hash or size.
    
    Strategy:
    1. If expected_hash provided: verify local file hash
    2. If server supports remote hash: verify via ssh (best)
    3. Fall back to size comparison with tolerance
    
    Returns:
        (success: bool, message: str)
    """
    # Check local file exists
    if not os.path.exists(local_path):
        return False, "File doesn't exist"
    
    local_size = os.path.getsize(local_path)
    
    # Option 1: Verify with expected hash (most reliable)
    if expected_hash:
        local_hash = calculate_file_hash(local_path, algorithm)
        if local_hash and local_hash.lower() == expected_hash.lower():
            return True, f"Hash verified ({algorithm})"
        if local_hash:
            return False, f"Hash mismatch: expected {expected_hash}, got {local_hash}"
    
    # Option 2: Calculate remote hash and compare with local
    if ssh:
        remote_hash = calculate_remote_hash(ssh, remote_path, algorithm)
        if remote_hash:
            local_hash = calculate_file_hash(local_path, algorithm)
            if local_hash and local_hash.lower() == remote_hash.lower():
                return True, f"Remote hash verified ({algorithm})"
            if local_hash:
                return False, f"Remote hash mismatch: remote={remote_hash}, local={local_hash}"
    
    # Option 3: Fall back to size comparison
    if expected_size:
        tolerance = max(int(expected_size * 0.001), 10)  # 0.1% or 10 bytes
        if abs(local_size - expected_size) <= tolerance:
            return True, f"Size verified (tolerance: {tolerance} bytes)"
        return False, f"Size mismatch: expected {expected_size}, got {local_size} (tolerance: {tolerance})"
    
    # No verification possible
    return True, "No verification performed"


def get_remote_file_info(ssh, sftp, remote_path: str) -> dict:
    """
    Get file info from remote server using multiple methods.
    
    Returns dict with:
    - size: File size in bytes
    - mtime: Modification time (unix timestamp)
    - hash: MD5 hash if available
    - is_symlink: True if path is a symlink
    """
    info = {'size': 0, 'mtime': 0, 'hash': None, 'is_symlink': False}
    
    try:
        # Try SFTP stat first
        if sftp:
            attr = sftp.stat(remote_path)
            info['size'] = attr.st_size
            info['mtime'] = attr.st_mtime
            
            # Check if symlink
            import stat
            if stat.S_ISLNK(attr.st_mode):
                info['is_symlink'] = True
            
            # Try to get hash via ssh
            info['hash'] = calculate_remote_hash(ssh, remote_path, 'md5')
            
    except Exception as e:
        logger.debug(f"Failed to get remote file info: {e}")
        
        # Fallback: try ssh exec_command with ls -l
        try:
            stdin, stdout, stderr = ssh.exec_command(
                f'ls -l --time-style=long-iso "{remote_path}"', timeout=30
            )
            out = stdout.read().decode('utf-8', errors='replace').strip()
            if out and not out.startswith('ls:'):
                parts = out.split()
                if len(parts) >= 5:
                    info['size'] = int(parts[4])
        except Exception:
            pass
    
    return info
