"""Response minimization only; this does not audit logging, storage or provider retention."""


def install_privacy_headers(app):
    @app.middleware("http")
    async def privacy_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/households/") or request.headers.get(
            "authorization"
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            vary = [
                v.strip()
                for v in response.headers.get("Vary", "").split(",")
                if v.strip()
            ]
            if "authorization" not in {v.lower() for v in vary}:
                vary.append("Authorization")
            response.headers["Vary"] = ", ".join(vary)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response