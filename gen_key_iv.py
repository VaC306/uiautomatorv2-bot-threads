# gen_key_iv.py
import os, binascii

key = binascii.hexlify(os.urandom(16)).decode()
iv  = binascii.hexlify(os.urandom(16)).decode()

print("key:", key)
print(" iv:", iv)
