
import requests
import jwt
import datetime
import os
import sys

# Configuration matches backend
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
API_URL = "http://localhost:8000/api"

def debug_auth():
    print(f"--- Debugging Auth ---")
    print(f"Current UTC Time: {datetime.datetime.utcnow()}")
    
    # 1. Login
    try:
        resp = requests.post(f"{API_URL}/auth/login", json={"username": "admin", "password": "admin123"})
        if resp.status_code != 200:
            print(f"Login failed: {resp.status_code} {resp.text}")
            return
        
        data = resp.json()
        token = data["access_token"]
        print(f"Login successful. Token obtained.")
        print(f"Token: {token[:20]}...")
    except Exception as e:
        print(f"Login exception: {e}")
        return

    # 2. Decode Token locally to check claims
    try:
        # Decode without verification first to see contents
        unverified = jwt.decode(token, options={"verify_signature": False})
        print(f"Token Claims (Unverified): {unverified}")
        
        exp_timestamp = unverified.get("exp")
        if exp_timestamp:
            exp_date = datetime.datetime.utcfromtimestamp(exp_timestamp)
            print(f"Token Expiry (UTC): {exp_date}")
            print(f"Time remaining: {exp_date - datetime.datetime.utcnow()}")
        
        # Verify signature
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"Local Signature Verification: Success")
    except jwt.ExpiredSignatureError:
        print(f"Local Signature Verification: FAILED (Expired)")
    except jwt.InvalidTokenError as e:
        print(f"Local Signature Verification: FAILED (Invalid: {e})")
    except Exception as e:
        print(f"Local Decode Error: {e}")

    # 3. Use Token against Admin API
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{API_URL}/admin/users", headers=headers)
        print(f"Admin API Request Status: {resp.status_code}")
        if resp.status_code == 200:
            print("Admin API Access: SUCCESS")
            users = resp.json()
            print(f"Users found: {len(users)}")
        else:
            print(f"Admin API Access: FAILED")
            print(f"Response: {resp.text}")
    except Exception as e:
        print(f"API Request Exception: {e}")

if __name__ == "__main__":
    try:
        debug_auth()
    except ImportError:
        print("Please install requests and pyjwt: pip install requests pyjwt")
