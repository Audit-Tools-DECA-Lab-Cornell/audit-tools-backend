"""Email templates for email service."""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_WEB_APP_URL = "https://audit-tools-playspace-frontend.vercel.app/"
_IOS_APP_URL = "https://apps.apple.com/app/id6755903317"
_ANDROID_APP_URL = "https://play.google.com/apps/internaltest/4701144847649057394"

# ─── Shared <head> additions ──────────────────────────────────────────────────
#
# _COLOR_SCHEME_META signals dark-mode intent to supporting clients before any
# CSS is parsed — critical for Apple Mail which reads this before rendering.
#
# _STYLE_BLOCK is injected into every email's <head>.
# • Normalisation  — -webkit-text-size-adjust, mso-table spacing resets, image
#                    rendering, and iOS/Gmail auto-data-detection suppression.
# • Dark mode      — Adapts the warm-brown palette for Apple Mail, iOS Mail,
#                    Samsung Mail, and the Android Gmail app. Gmail web strips
#                    <style> blocks entirely, so those recipients fall back to
#                    the inline-styled light version — which is fine.
# • Mobile ≤ 600px — Card goes full-width and paddings reduce so the email
#                    reads comfortably on a phone without horizontal scroll.
#
# CSS classes (e.g. .email-bg, .email-card) are added to key HTML elements so
# media-query rules can override their inline styles with !important.

_COLOR_SCHEME_META = """\
  <meta name="color-scheme" content="light dark" />
  <meta name="supported-color-schemes" content="light dark" />"""

_STYLE_BLOCK = """\
<style>
  /* ── Client normalisation ─────────────────────────────────────────────────── */
  body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
  table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
  img { border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }
  /* Stop iOS and Gmail from auto-linking phone numbers, dates, and postal addresses */
  a[x-apple-data-detectors] { color: inherit !important; text-decoration: none !important; font-size: inherit !important; }
  .x-gmail-data-detectors, .x-gmail-data-detectors * { color: inherit !important; text-decoration: none !important; }

  /* ── Dark mode ────────────────────────────────────────────────────────────── */
  @media (prefers-color-scheme: dark) {
    .email-bg      { background-color: #1a120a !important; }
    .email-card    { background-color: #2d1f14 !important; }
    .email-text    { color: #f0e4d4 !important; }
    .email-muted   { color: #c4a886 !important; }
    .email-label   { color: #e8c99a !important; }
    .email-divider { border-top-color: #4a3020 !important; }
    .creds-card    { background-color: #3a2410 !important; }
    .creds-sep td  { border-bottom-color: #4a3020 !important; }
    .warning-cell  { background-color: #3a2400 !important; }
    .warning-text  { color: #f5c876 !important; }
    .btn-outline   { color: #e8c99a !important; border-color: #e8c99a !important; background-color: #2d1f14 !important; }
    .step-text     { color: #f0e4d4 !important; }
    .step-num      { color: #e8c99a !important; }
  }

  /* ── Mobile (≤ 600 px) ───────────────────────────────────────────────────── */
  @media only screen and (max-width: 600px) {
    .email-card    { width: 100% !important; border-radius: 0 !important; }
    .email-outer   { padding: 0 !important; }
    .email-header  { padding: 24px 20px 18px 20px !important; }
    .email-content { padding: 24px 20px !important; }
    .email-footer  { padding: 20px !important; }
    .app-cell      { display: block !important; width: 100% !important; padding: 0 0 8px 0 !important; }
    .btn-outline   { box-sizing: border-box !important; width: 100% !important; }
  }
</style>"""


def _add_utm_params(
	url: str,
	*,
	campaign: str,
	content: str,
	source: str = "email",
	medium: str = "transactional",
) -> str:
	"""Append UTM tracking parameters to *url*, preserving any existing query params.

	UTM params enable end-to-end attribution in Google Analytics / Plausible for
	every link click that originates from a transactional email. Existing params in
	*url* are kept intact; UTM keys override any same-named params already present.

	Separate ``content`` values for the primary CTA button vs. the plain-text fallback
	link (e.g. ``"verify_cta"`` vs. ``"plain_link"``) let you identify users whose
	email clients failed to render the HTML button correctly.
	"""
	parsed = urlparse(url)
	existing: dict[str, str] = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
	utm: dict[str, str] = {
		"utm_source": source,
		"utm_medium": medium,
		"utm_campaign": campaign,
		"utm_content": content,
	}
	merged = {**existing, **utm}
	return urlunparse(parsed._replace(query=urlencode(merged)))


def credentials_html(
	full_name: str, to_email: str, auditor_code: str, temporary_password: str, platform: str, product: str
) -> str:
	web_url = _add_utm_params(_WEB_APP_URL, campaign="auditor_credentials", content="web_dashboard_cta")
	ios_url = _add_utm_params(_IOS_APP_URL, campaign="auditor_credentials", content="ios_app")
	android_url = _add_utm_params(_ANDROID_APP_URL, campaign="auditor_credentials", content="android_app")

	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
{_COLOR_SCHEME_META}
  <title>Your {product} Auditor Account</title>
  {_STYLE_BLOCK}
</head>
<body class="email-bg" style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span class="preheader" style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    Your {product} auditor credentials are ready — please sign in and update your password immediately.
  </span>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    class="email-bg email-outer" style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        class="email-card" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <!-- Header -->
        <tr>
          <td class="email-header" style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <img src="https://audit-tools-playspace-frontend.vercel.app/icon.png"
                alt="{product} Logo" width="64" height="64"
                style="display:block;margin:0 auto 16px auto;border-radius:14px;" />
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;">
              {platform}
            </p>
            <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
              Your Auditor Account<br/>Is Ready
            </h1>
          </td>
        </tr>

        <!-- Greeting -->
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
            <p class="email-text" style="margin:0 0 20px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Hello <strong>{full_name}</strong>,
            </p>
            <p class="email-text" style="margin:0 0 28px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              A {product} auditor account has been created for you by your workspace manager.
              Your login credentials are listed below. Please treat this information as
              confidential and update your password upon first sign-in.
            </p>
          </td>
        </tr>

        <!-- Credentials card -->
        <tr>
          <td class="email-content" style="padding:0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
              class="creds-card" style="background-color:#fdf6ee;border-radius:8px;border-left:4px solid #7a4f2e;">
              <tr>
                <td style="padding:24px 28px;">
                  <p class="email-label" style="margin:0 0 6px 0;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;">
                    Login Credentials
                  </p>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    class="creds-sep" style="margin-top:14px;border-bottom:1px solid #e8d5bf;padding-bottom:12px;">
                    <tr>
                      <td class="email-label" style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Email</td>
                      <td class="email-text" style="font-size:15px;color:#3d2a1a;font-weight:600;">{to_email}</td>
                    </tr>
                  </table>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    class="creds-sep" style="margin-top:12px;border-bottom:1px solid #e8d5bf;padding-bottom:12px;">
                    <tr>
                      <td class="email-label" style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Auditor Code</td>
                      <td class="email-text" style="font-family:'Courier New',Courier,monospace;font-size:17px;color:#3d2a1a;font-weight:700;letter-spacing:2px;">{auditor_code}</td>
                    </tr>
                  </table>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    style="margin-top:12px;">
                    <tr>
                      <td class="email-label" style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Temp. Password</td>
                      <td class="email-text" style="font-family:'Courier New',Courier,monospace;font-size:17px;color:#3d2a1a;font-weight:700;letter-spacing:2px;">{temporary_password}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Warning banner -->
        <tr>
          <td class="email-content" style="padding:20px 40px 0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
              style="border-radius:6px;border-left:4px solid #c8860a;">
              <tr>
                <td class="warning-cell" style="padding:14px 18px;background-color:#fff8e6;border-radius:6px;">
                  <p class="warning-text" style="margin:0;font-size:13px;color:#7a5000;line-height:1.6;">
                    &#9888;&#65039; <strong>Action required:</strong> Sign in and change your temporary
                    password immediately. Temporary credentials are short-lived and should never be shared.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Getting started steps -->
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
            <p class="email-label" style="margin:0 0 14px 0;font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;">
              Getting Started
            </p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
              <tr>
                <td class="step-num" style="width:24px;vertical-align:top;padding:4px 0;font-size:14px;font-weight:700;color:#7a4f2e;font-family:Georgia,serif;">1.</td>
                <td class="step-text" style="vertical-align:top;padding:4px 0 10px 10px;font-size:14px;color:#3d2a1a;line-height:1.6;">Sign in using the credentials above via the web dashboard or mobile app below.</td>
              </tr>
              <tr>
                <td class="step-num" style="width:24px;vertical-align:top;padding:4px 0;font-size:14px;font-weight:700;color:#7a4f2e;font-family:Georgia,serif;">2.</td>
                <td class="step-text" style="vertical-align:top;padding:4px 0 10px 10px;font-size:14px;color:#3d2a1a;line-height:1.6;">Go to <strong>Account Settings</strong> and update your password immediately.</td>
              </tr>
              <tr>
                <td class="step-num" style="width:24px;vertical-align:top;padding:4px 0;font-size:14px;font-weight:700;color:#7a4f2e;font-family:Georgia,serif;">3.</td>
                <td class="step-text" style="vertical-align:top;padding:4px 0 4px 10px;font-size:14px;color:#3d2a1a;line-height:1.6;">Begin your assigned audits from the <strong>My Assignments</strong> dashboard.</td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Sign-in section -->
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
            <p class="email-label" style="margin:0 0 16px 0;font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;">
              Sign In &amp; Reset Your Password
            </p>
            <!-- Web dashboard — primary CTA -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-bottom:12px;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{web_url}"
                    style="display:block;padding:13px 24px;font-family:Georgia,serif;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;text-align:center;">
                    &#127760; Open Web Dashboard &rarr;
                  </a>
                </td>
              </tr>
            </table>
            <!-- Mobile app buttons — text-only for maximum email client compatibility -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;">
              <tr>
                <td class="app-cell" style="width:49%;padding-right:6px;">
                  <a href="{ios_url}" class="btn-outline"
                    style="display:block;padding:12px 8px;font-family:Georgia,serif;font-size:13px;font-weight:600;color:#7a4f2e;text-decoration:none;border-radius:8px;text-align:center;border:2px solid #7a4f2e;background-color:#ffffff;">
                    iOS App Store &rarr;
                  </a>
                </td>
                <td class="app-cell" style="width:49%;padding-left:6px;">
                  <a href="{android_url}" class="btn-outline"
                    style="display:block;padding:12px 8px;font-family:Georgia,serif;font-size:13px;font-weight:600;color:#7a4f2e;text-decoration:none;border-radius:8px;text-align:center;border:2px solid #7a4f2e;background-color:#ffffff;">
                    Android App &rarr;
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Closing note -->
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
            <p class="email-muted" style="margin:0;font-size:14px;color:#7a6050;line-height:1.7;">
              If you were not expecting this account or believe it was created in error,
              please contact your workspace administrator immediately and disregard this message.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td class="email-footer" style="padding:28px 40px 36px 40px;">
            <hr class="email-divider" style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p class="email-muted" style="margin:0;font-size:12px;color:#b09070;text-align:center;line-height:1.8;">
              This is an automated message from <strong>{platform}</strong>.<br/>
              Please do not reply directly to this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def invite_html(invite_url: str, role: str) -> str:
	role_label = "manager" if role == "manager" else "auditor"
	role_action = (
		"set your password and configure your workspace" if role == "manager" else "create your account and begin setup"
	)
	campaign = "manager_invite" if role == "manager" else "auditor_invite"
	expiry_days = 7 if role == "manager" else 7
	invite_btn_url = _add_utm_params(invite_url, campaign=campaign, content="accept_invitation_cta")
	invite_plain_url = _add_utm_params(invite_url, campaign=campaign, content="plain_link")

	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
{_COLOR_SCHEME_META}
  <title>You've Been Invited to Audit Tools</title>
  {_STYLE_BLOCK}
</head>
<body class="email-bg" style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span class="preheader" style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    You've been invited to join an Audit Tools workspace as a {role_label}. Accept your invite to get started.
  </span>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    class="email-bg email-outer" style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        class="email-card" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <!-- Header -->
        <tr>
          <td class="email-header" style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;">
              Audit Tools
            </p>
            <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
              You've Been Invited
            </h1>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td class="email-content" style="padding:36px 40px 28px 40px;">
            <p class="email-text" style="margin:0 0 16px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              You have been invited to join an Audit Tools workspace as a <strong>{role_label}</strong>.
            </p>
            <p class="email-text" style="margin:0 0 28px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Click the button below to {role_action}. This invitation link is unique to you
              — please do not share it.
            </p>

            <!-- Primary CTA button -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{invite_btn_url}"
                    style="display:inline-block;padding:14px 36px;font-family:Georgia,serif;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">
                    Accept Invitation &rarr;
                  </a>
                </td>
              </tr>
            </table>

            <!-- Fallback plain link -->
            <p class="email-muted" style="margin:20px 0 0 0;font-size:12px;color:#9a7050;text-align:center;line-height:1.6;">
              If the button above doesn't work, copy and paste this link into your browser:<br/>
              <a href="{invite_plain_url}" style="color:#7a4f2e;word-break:break-all;">{invite_url}</a>
            </p>
            <p class="email-muted" style="margin:10px 0 0 0;font-size:11px;color:#b09070;text-align:center;">
              This invitation link expires in {expiry_days} days.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td class="email-footer" style="padding:0 40px 36px 40px;">
            <hr class="email-divider" style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p class="email-muted" style="margin:0;font-size:13px;color:#7a6050;line-height:1.7;">
              If you were not expecting this invitation, you can safely ignore this email.
              No account will be created without your action.
            </p>
            <p class="email-muted" style="margin:16px 0 0 0;font-size:12px;color:#b09070;text-align:center;">
              Automated message from <strong>Audit Tools</strong> &mdash; please do not reply.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def verification_html(verify_url: str) -> str:
	verify_btn_url = _add_utm_params(verify_url, campaign="email_verification", content="verify_cta")
	verify_plain_url = _add_utm_params(verify_url, campaign="email_verification", content="plain_link")

	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
{_COLOR_SCHEME_META}
  <title>Verify Your Audit Tools Account</title>
  {_STYLE_BLOCK}
</head>
<body class="email-bg" style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span class="preheader" style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    One more step — verify your email to activate your Audit Tools account.
  </span>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    class="email-bg email-outer" style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        class="email-card" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <!-- Header -->
        <tr>
          <td class="email-header" style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;">
              Audit Tools
            </p>
            <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
              Verify Your Email Address
            </h1>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td class="email-content" style="padding:36px 40px 28px 40px;">
            <p class="email-text" style="margin:0 0 24px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Welcome to Audit Tools! To activate your account, please confirm your
              email address by clicking the button below.
            </p>

            <!-- Primary CTA button -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{verify_btn_url}"
                    style="display:inline-block;padding:14px 36px;font-family:Georgia,serif;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">
                    Verify My Email &rarr;
                  </a>
                </td>
              </tr>
            </table>

            <!-- Fallback plain link -->
            <p class="email-muted" style="margin:20px 0 0 0;font-size:12px;color:#9a7050;text-align:center;line-height:1.6;">
              Or copy this link into your browser:<br/>
              <a href="{verify_plain_url}" style="color:#7a4f2e;word-break:break-all;">{verify_url}</a>
            </p>
            <p class="email-muted" style="margin:10px 0 0 0;font-size:11px;color:#b09070;text-align:center;">
              This link expires in 24 hours. If it has expired, sign in to request a new one.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td class="email-footer" style="padding:0 40px 36px 40px;">
            <hr class="email-divider" style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p class="email-muted" style="margin:0;font-size:13px;color:#7a6050;line-height:1.7;">
              If you did not create an Audit Tools account, you can safely ignore this email.
            </p>
            <p class="email-muted" style="margin:16px 0 0 0;font-size:12px;color:#b09070;text-align:center;">
              Automated message from <strong>Audit Tools</strong> &mdash; please do not reply.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
