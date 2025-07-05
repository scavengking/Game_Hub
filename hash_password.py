from werkzeug.security import generate_password_hash

# Enter the password you want to use for the admin account
plain_text_password = "admin123"

# Generate the secure hash
hashed_password = generate_password_hash(plain_text_password)

print("Copy this entire line and paste it into your database:")
print(hashed_password)