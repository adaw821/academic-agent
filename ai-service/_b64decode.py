import base64, sys
b64 = sys.argv[1]
path = sys.argv[2]
data = base64.b64decode(b64).decode("utf-8")
with open(path, "w", encoding="utf-8") as f:
    f.write(data)
print(f"Written {len(data)} chars to {path}")