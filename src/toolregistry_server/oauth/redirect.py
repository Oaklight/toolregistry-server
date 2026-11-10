"""Swagger UI's OAuth2 redirect page.

Swagger UI finishes the authorization-code flow by opening
``<page directory>/oauth2-redirect.html`` after the authorization server
redirects the browser back.  The server serves that page itself, because the
built-in ``/docs`` page is Swagger UI loaded from a CDN and the CDN copy does
not know this deployment's location.

The page is deliberately self-contained: it loads no external asset, and it
mirrors the parsing and callback logic of the official
``swagger-ui-dist@5.34.1/oauth2-redirect.js`` file.  It reads the
authorization response from the URL, hands it back to the opener window
through ``swaggerUIRedirectOauth2``, and closes the popup.

The callback parameters are percent-encoded by the authorization server, so
each value is passed through ``decodeURIComponent`` while the response is
parsed.  Without that step a ``code`` containing ``+``, ``/``, ``=`` or
``%`` reaches Swagger UI still encoded and the token exchange fails with
``invalid_grant``.  The hash form also drops a leading ``?`` the way upstream
does, so a callback shaped ``#?code=...`` parses.

The route is unauthenticated: the authorization server redirects the browser
without a bearer token, so a token check would turn the callback into a
``401`` and leave the flow unfinished (#245).
"""

from __future__ import annotations

#: Path of the redirect page, relative to the serving frontend root.
OAUTH2_REDIRECT_PATH = "/oauth2-redirect.html"

#: Value Swagger UI resolves against the ``/docs`` page URL.  It is relative
#: on purpose: the same page works at the listener root and under a gateway
#: mount path such as ``/openapi``.
OAUTH2_REDIRECT_URL = "oauth2-redirect.html"

#: The static page.  Kept as one literal so both backends serve byte-identical
#: content and a test can compare the route body.
OAUTH2_REDIRECT_HTML = """<!DOCTYPE html>
<html lang="en-US">
<head>
  <meta charset="utf-8">
  <title>Swagger UI: OAuth2 Redirect</title>
</head>
<body>
<script>
'use strict';
(function () {
  function run() {
    var oauth2 = window.opener && window.opener.swaggerUIRedirectOauth2;
    var sentState = oauth2 && oauth2.state;
    var redirectUrl = oauth2 && oauth2.redirectUrl;
    var isValid, qp, arr;

    if (!oauth2) {
      return;
    }

    if (/code|token|error/.test(window.location.hash)) {
      // A hash may carry a leading "?", as in "#?code=...".
      qp = window.location.hash.substring(1).replace("?", "&");
    } else {
      qp = window.location.search.substring(1);
    }

    // An empty segment (a leading or trailing "&") would break the JSON.
    arr = qp.split("&").filter(function (v) { return v !== ""; });
    arr.forEach(function (v, i, _arr) {
      _arr[i] = '"' + v.replace("=", '":"') + '"';
    });
    qp = qp
      ? JSON.parse("{" + arr.join() + "}", function (key, value) {
          return key === "" ? value : decodeURIComponent(value);
        })
      : {};
    isValid = qp.state === sentState;

    if ((oauth2.auth.schema.get("flow") === "accessCode" ||
         oauth2.auth.schema.get("flow") === "authorizationCode" ||
         oauth2.auth.schema.get("flow") === "authorization_code") &&
        !oauth2.auth.code) {
      if (!isValid) {
        oauth2.errCb({
          authId: oauth2.auth.name,
          source: "auth",
          level: "warning",
          message: "Authorization may be unsafe, passed state was changed in " +
            "server. The passed state wasn't returned from auth server."
        });
      }
      if (qp.code) {
        delete oauth2.state;
        oauth2.auth.code = qp.code;
        oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl});
      } else {
        var oauthErrorMsg;
        if (qp.error) {
          oauthErrorMsg = "[" + qp.error + "]: " +
            (qp.error_description ? qp.error_description + ". " : "") +
            "no accessCode received from the server. ";
        }
        oauth2.errCb({
          authId: oauth2.auth.name,
          source: "auth",
          level: "error",
          message: oauthErrorMsg ||
            "[Authorization failed]: no accessCode received from the server."
        });
      }
    } else {
      oauth2.callback({
        auth: oauth2.auth,
        token: qp,
        isValid: isValid,
        redirectUrl: redirectUrl
      });
    }
    window.close();
  }

  if (document.readyState !== "loading") {
    run();
  } else {
    document.addEventListener("DOMContentLoaded", function () { run(); });
  }
})();
</script>
</body>
</html>
"""


def oauth2_redirect_html() -> str:
    """Return the Swagger UI OAuth2 redirect page.

    Returns:
        The static HTML document, with no external references.
    """
    return OAUTH2_REDIRECT_HTML


__all__ = [
    "OAUTH2_REDIRECT_HTML",
    "OAUTH2_REDIRECT_PATH",
    "OAUTH2_REDIRECT_URL",
    "oauth2_redirect_html",
]
