import hashlib
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

def decrypt_api_key(encrypted_hex: str, iv_hex: str) -> str:
    """Decrypt an API key encrypted using Node's crypto aes-256-cbc.
    
    The encryption was done using Node.js crypto:
    - Algorithm: aes-256-cbc
    - Key: sha256(ENCRYPTION_KEY)
    - Padding: PKCS7
    """
    encryption_key = os.getenv("ENCRYPTION_KEY")
    if not encryption_key:
        from app.config import settings
        encryption_key = settings.ENCRYPTION_KEY
        
    if not encryption_key:
        raise ValueError("Missing ENCRYPTION_KEY environment variable")

    # Node's crypto with 'sha256' hashed key
    key = hashlib.sha256(encryption_key.encode()).digest()
    iv = bytes.fromhex(iv_hex)
    encrypted_data = bytes.fromhex(encrypted_hex)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    
    padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
    
    # Node's crypto uses PKCS7 padding by default
    unpadder = padding.PKCS7(128).unpadder()
    data = unpadder.update(padded_data) + unpadder.finalize()
    
    return data.decode("utf-8")
