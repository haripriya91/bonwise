"""Private Storage REST calls. Never accept URLs/object paths from the browser."""
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.ai.image_extractor import MAX_OUTPUT_BYTES
from app.core.config import get_settings


class ReceiptStorageError(Exception):
    pass


class ReceiptMissing(ReceiptStorageError):
    pass


class ReceiptConflict(ReceiptStorageError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, path, *, data=None, headers=None, limit=8192):
    settings = get_settings()
    key = settings.supabase_secret_key.get_secret_value()
    auth = {"apikey": key}
    # New sb_secret keys go only in apikey; legacy service-role JWTs also use Bearer.
    if key.startswith("eyJ"):
        auth["Authorization"] = "Bearer " + key
    auth.update(headers or {})
    url = str(settings.supabase_url).rstrip("/") + "/storage/v1/" + path
    req = Request(url, data=data, headers=auth, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=15) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ReceiptStorageError()
            return body
    except HTTPError as error:
        code = None
        try:
            parsed = json.loads(error.read(4096))
            if isinstance(parsed, dict):
                code = parsed.get("code") or parsed.get("error") or str(parsed.get("statusCode", ""))
        except (ValueError, OSError):
            pass
        if error.code == 404 or code in {"NoSuchKey", "not_found", "404"}:
            raise ReceiptMissing() from None
        if error.code == 409 or code in {"Duplicate", "ResourceAlreadyExists", "KeyAlreadyExists"}:
            raise ReceiptConflict() from None
        raise ReceiptStorageError() from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ReceiptStorageError() from None


def bucket_path():
    return quote(get_settings().supabase_storage_bucket, safe="")


def require_private_bucket():
    try:
        data = json.loads(request("GET", "bucket/" + bucket_path()))
        if not isinstance(data, dict) or data.get("public") is not False:
            raise ReceiptStorageError()
    except (ValueError, TypeError):
        raise ReceiptStorageError() from None


def download(key, expected_sha256):
    require_private_bucket()
    content = request("GET", "object/authenticated/" + bucket_path() + "/" +
                      quote(key, safe="/"), limit=MAX_OUTPUT_BYTES)
    if (not content.startswith(b"\xff\xd8\xff")
        or hashlib.sha256(content).hexdigest() != expected_sha256):
        raise ReceiptStorageError()
    return content


def upload(key, content, expected_sha256):
    require_private_bucket()
    try:
        request("POST", "object/" + bucket_path() + "/" + quote(key, safe="/"),
                data=content, headers={"Content-Type": "image/jpeg", "x-upsert": "false",
                                       "Cache-Control": "0"})
    except ReceiptConflict:
        # Retry of identical content is allowed; never overwrite a stored object.
        if download(key, expected_sha256) != content:
            raise ReceiptStorageError()


def delete(key):
    require_private_bucket()
    try:
        request("DELETE", "object/" + bucket_path() + "/" + quote(key, safe="/"))
    except ReceiptMissing:
        pass