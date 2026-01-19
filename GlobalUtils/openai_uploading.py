from pathlib import Path
import hashlib
import json
from openai import AsyncOpenAI

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def load_cache(cache_path) -> dict:
    cache_path = cache_path
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}

def save_cache(cache_path: Path, cache: dict):
    cache_path.write_text(json.dumps(cache))

async def get_or_upload_async(file_path: str, client: AsyncOpenAI, cache_path: str, purpose = "user_data") -> str:
    cache_path = Path(cache_path)
    file_path = Path(file_path)
    cache = load_cache(cache_path)
    digest = sha256(file_path)

    # 1. Cache hit ➜ just return the ID
    if digest in cache:
        return cache[digest]

    # 2. Cache miss ➜ upload once
    with open(file_path, "rb") as f:
        creation_resp = await client.files.create(file=f, purpose=purpose)
        file_id = creation_resp.id

    # 3. Remember it for next time
    cache[digest] = file_id
    save_cache(cache_path, cache)
    return file_id

# async def get_or_upload_stream_async(file_stream, client: AsyncOpenAI, cache_path: str, purpose = "user_data", file_type = 'application/pdf', file_name = 'document.pdf') -> str:
#     if isinstance(file_stream, BytesIO):
#         file_stream = file_stream.read()
#     cache_path = Path(cache_path)
#     file_stream.seek(0)
#     file_bytes = file_stream.read()
#     digest = hashlib.sha256(file_bytes).hexdigest()
#
#     cache = load_cache(cache_path)
#
#     # 1. Cache hit ➜ just return the ID
#     if digest in cache:
#         return cache[digest]
#
#     # 2. Cache miss ➜ upload once
#     creation_resp = await client.files.create(file=(file_name, file_stream, file_type), purpose=purpose)
#     file_id = creation_resp.id
#
#     # 3. Remember it for next time
#     cache[digest] = file_id
#     save_cache(cache_path, cache)
#     return file_id