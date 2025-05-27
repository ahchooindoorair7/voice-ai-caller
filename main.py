10.214.133.255 - - [27/May/2025 00:05:23] "GET /authorize HTTP/1.1" 302 -
[2025-05-27 00:05:30,517] ERROR in app: Exception on /oauth2callback [GET]
Traceback (most recent call last):
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/flask/app.py", line 1511, in wsgi_app
    response = self.full_dispatch_request()
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/flask/app.py", line 919, in full_dispatch_request
    rv = self.handle_user_exception(e)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/flask/app.py", line 917, in full_dispatch_request
    rv = self.dispatch_request()
         ^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/flask/app.py", line 902, in dispatch_request
    return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)  # type: ignore[no-any-return]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/main.py", line 52, in oauth2callback
    flow.fetch_token(authorization_response=request.url)
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/google_auth_oauthlib/flow.py", line 285, in fetch_token
    return self.oauth2session.fetch_token(self.client_config["token_uri"], **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/requests_oauthlib/oauth2_session.py", line 271, in fetch_token
    self._client.parse_request_uri_response(
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/oauthlib/oauth2/rfc6749/clients/web_application.py", line 220, in parse_request_uri_response
    response = parse_authorization_code_response(uri, state=state)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.11/site-packages/oauthlib/oauth2/rfc6749/parameters.py", line 272, in parse_authorization_code_response
    raise InsecureTransportError()
oauthlib.oauth2.rfc6749.errors.InsecureTransportError: (insecure_transport) OAuth 2 MUST utilize https.
10.214.133.255 - - [27/May/2025 00:05:30] "GET /oauth2callback?state=8PcpfIJWXlMhazwTvPFDmmF6iLxmht&code=4/0AUJR-x5qg81xlfplabLsFFQ6CyVFgkZos5wmX7V_e7ATQmkkoZow-JwtYuyhTo6Xx-419g&scope=https://www.googleapis.com/auth/calendar.readonly HTTP/1.1" 500 -
