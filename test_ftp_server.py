import os
import shutil
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

# Mock FTP server configuration
FTP_HOST = '127.0.0.1'
FTP_PORT = 2121
FTP_USER = 'testuser'
FTP_PASSWORD = 'testpassword'
FTP_ROOT = './mock_ftp_root'

def setup_mock_ftp():
    if os.path.exists(FTP_ROOT):
        shutil.rmtree(FTP_ROOT)
    os.makedirs(FTP_ROOT)
    
    # Create some initial files
    with open(os.path.join(FTP_ROOT, 'file1.txt'), 'w') as f:
        f.write("Initial content of file 1")
        
    os.makedirs(os.path.join(FTP_ROOT, 'subdir'))
    with open(os.path.join(FTP_ROOT, 'subdir', 'file2.txt'), 'w') as f:
        f.write("Initial content of file 2")

def start_server():
    setup_mock_ftp()
    
    authorizer = DummyAuthorizer()
    authorizer.add_user(FTP_USER, FTP_PASSWORD, FTP_ROOT, perm='elradfmw')
    
    handler = FTPHandler
    handler.authorizer = authorizer
    
    server = FTPServer((FTP_HOST, FTP_PORT), handler)
    print(f"Mock FTP server started at {FTP_HOST}:{FTP_PORT}")
    server.serve_forever()

if __name__ == "__main__":
    start_server()
