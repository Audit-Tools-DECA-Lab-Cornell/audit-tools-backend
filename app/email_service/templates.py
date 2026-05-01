"""Email templates for email service."""

_WEB_APP_URL = "https://audit-tools-playspace-frontend.vercel.app/"
_IOS_APP_URL = "https://apps.apple.com/app/id6755903317"
_ANDROID_APP_URL = "https://play.google.com/apps/internaltest/4701144847649057394"


def credentials_html(
	full_name: str, to_email: str, auditor_code: str, temporary_password: str, platform: str, product: str
) -> str:
	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <title>Your {product} Auditor Account</title>
</head>
<body style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    Your {product} auditor credentials are ready — please sign in and update your password immediately.
  </span>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;
               overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <!-- Header -->
        <tr>
          <td style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <img src="https://audit-tools-playspace-frontend.vercel.app/icon.png"
                alt="{product} Logo"
                width="64" height="64"
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
          <td style="padding:36px 40px 0 40px;">
            <p style="margin:0 0 20px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Hello <strong>{full_name}</strong>,
            </p>
            <p style="margin:0 0 28px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              A {product} auditor account has been created for you by your workspace manager.
              Your login credentials are listed below. Please treat this information as
              confidential and update your password upon first sign-in.
            </p>
          </td>
        </tr>

        <!-- Credentials card -->
        <tr>
          <td style="padding:0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
              style="background-color:#fdf6ee;border-radius:8px;border-left:4px solid #7a4f2e;">
              <tr>
                <td style="padding:24px 28px;">
                  <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;">
                    Login Credentials
                  </p>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    style="margin-top:14px;border-bottom:1px solid #e8d5bf;padding-bottom:12px;">
                    <tr>
                      <td style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Email</td>
                      <td style="font-size:15px;color:#3d2a1a;font-weight:600;">{to_email}</td>
                    </tr>
                  </table>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    style="margin-top:12px;border-bottom:1px solid #e8d5bf;padding-bottom:12px;">
                    <tr>
                      <td style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Auditor Code</td>
                      <td style="font-family:'Courier New',Courier,monospace;font-size:17px;color:#3d2a1a;font-weight:700;letter-spacing:2px;">{auditor_code}</td>
                    </tr>
                  </table>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                    style="margin-top:12px;">
                    <tr>
                      <td style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:130px;">Temp. Password</td>
                      <td style="font-family:'Courier New',Courier,monospace;font-size:17px;color:#3d2a1a;font-weight:700;letter-spacing:2px;">{temporary_password}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Warning banner -->
        <tr>
          <td style="padding:20px 40px 0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
              style="background-color:#fff8e6;border-radius:6px;border-left:4px solid #c8860a;">
              <tr>
                <td style="padding:14px 18px;font-size:13px;color:#7a5000;line-height:1.6;">
                  &#9888;&#65039; <strong>Action required:</strong> Sign in and change your temporary
                  password immediately. Temporary credentials are short-lived and should never be shared.
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Sign-in links section -->
        <tr>
          <td style="padding:28px 40px 0 40px;">
            <p style="margin:0 0 16px 0;font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;">
              Sign In &amp; Reset Your Password
            </p>
            <!-- Web dashboard button -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-bottom:12px;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{_WEB_APP_URL}"
                    style="display:block;padding:13px 24px;font-family:Georgia,serif;font-size:14px;
                           font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;text-align:center;">
                    &#127760; Open Web Dashboard &rarr;
                  </a>
                </td>
              </tr>
            </table>
            <!-- Mobile app buttons -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;">
              <tr>
                <td style="width:49%;padding-right:6px;">
                  <a href="{_IOS_APP_URL}"
                    style="display:block;padding:12px 8px;font-family:Georgia,serif;font-size:13px;
                          font-weight:600;color:#7a4f2e;text-decoration:none;border-radius:8px;text-align:center;
                          border:2px solid #7a4f2e;background-color:#ffffff;">
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
                      <tr>
                        <td valign="middle" style="padding-right:6px;line-height:0;">
                          <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/f/fa/Apple_logo_black.svg/64px-Apple_logo_black.svg.png"
                              alt="Apple" width="14" height="17"
                              style="display:block;" />
                        </td>
                        <td valign="middle" style="font-family:Georgia,serif;font-size:13px;font-weight:600;color:#7a4f2e;">
                          Download on iOS
                        </td>
                      </tr>
                    </table>
                  </a>
                </td>

                <td style="width:49%;padding-left:6px;">
                  <a href="{_ANDROID_APP_URL}"
                    style="display:block;padding:12px 8px;font-family:Georgia,serif;font-size:13px;
                          font-weight:600;color:#7a4f2e;text-decoration:none;border-radius:8px;text-align:center;
                          border:2px solid #7a4f2e;background-color:#ffffff;">
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
                      <tr>
                        <td valign="middle" style="padding-right:6px;line-height:0;">
                          <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Google_Play_2022_icon.svg/64px-Google_Play_2022_icon.svg.png"
                              alt="Google Play" width="16" height="18"
                              style="display:block;" />
                        </td>
                        <td valign="middle" style="font-family:Georgia,serif;font-size:13px;font-weight:600;color:#7a4f2e;">
                          Get it on Android
                        </td>
                      </tr>
                    </table>
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Closing note -->
        <tr>
          <td style="padding:28px 40px 0 40px;">
            <p style="margin:0;font-size:14px;color:#7a6050;line-height:1.7;">
              If you were not expecting this account or believe it was created in error,
              please contact your workspace administrator immediately and disregard this message.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:28px 40px 36px 40px;">
            <hr style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p style="margin:0;font-size:12px;color:#b09070;text-align:center;line-height:1.8;">
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
	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>You've Been Invited to Audit Tools</title>
</head>
<body style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    You've been invited to join an Audit Tools workspace as a {role_label}. Accept your invite to get started.
  </span>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;
               overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <tr>
          <td style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;">
              Audit Tools
            </p>
            <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
              You've Been Invited
            </h1>
          </td>
        </tr>

        <tr>
          <td style="padding:36px 40px 28px 40px;">
            <p style="margin:0 0 16px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              You have been invited to join an Audit Tools workspace as a <strong>{role_label}</strong>.
            </p>
            <p style="margin:0 0 28px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Click the button below to {role_action}. This invitation link is unique to you
              — please do not share it.
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{invite_url}"
                    style="display:inline-block;padding:14px 32px;font-family:Georgia,serif;
                           font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;
                           border-radius:8px;">
                    Accept Invitation &rarr;
                  </a>
                </td>
              </tr>
            </table>
            <p style="margin:24px 0 0 0;font-size:12px;color:#9a7050;text-align:center;line-height:1.6;">
              If the button above doesn't work, copy and paste this link into your browser:<br/>
              <a href="{invite_url}" style="color:#7a4f2e;word-break:break-all;">{invite_url}</a>
            </p>
          </td>
        </tr>

        <tr>
          <td style="padding:0 40px 36px 40px;">
            <hr style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p style="margin:0;font-size:13px;color:#7a6050;line-height:1.7;">
              If you were not expecting this invitation, you can safely ignore this email.
              No account will be created without your action.
            </p>
            <p style="margin:16px 0 0 0;font-size:12px;color:#b09070;text-align:center;">
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
	return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Verify Your Audit Tools Account</title>
</head>
<body style="margin:0;padding:0;background-color:#f5ede0;font-family:Georgia,'Times New Roman',serif;">
  <span style="display:none;font-size:1px;color:#f5ede0;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    One more step — verify your email to activate your Audit Tools account.
  </span>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    style="background-color:#f5ede0;padding:40px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
        style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;
               overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">

        <tr>
          <td style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;">
              Audit Tools
            </p>
            <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
              Verify Your Email Address
            </h1>
          </td>
        </tr>

        <tr>
          <td style="padding:36px 40px 28px 40px;">
            <p style="margin:0 0 16px 0;font-size:15px;color:#3d2a1a;line-height:1.7;">
              Welcome to Audit Tools! To activate your account, please confirm your
              email address by clicking the button below.
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 auto;">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{verify_url}"
                    style="display:inline-block;padding:14px 32px;font-family:Georgia,serif;
                           font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">
                    Verify My Email &rarr;
                  </a>
                </td>
              </tr>
            </table>
            <p style="margin:24px 0 0 0;font-size:12px;color:#9a7050;text-align:center;line-height:1.6;">
              Or copy this link into your browser:<br/>
              <a href="{verify_url}" style="color:#7a4f2e;word-break:break-all;">{verify_url}</a>
            </p>
          </td>
        </tr>

        <tr>
          <td style="padding:0 40px 36px 40px;">
            <hr style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p style="margin:0;font-size:13px;color:#7a6050;line-height:1.7;">
              If you did not create an Audit Tools account, you can safely ignore this email.
            </p>
            <p style="margin:16px 0 0 0;font-size:12px;color:#b09070;text-align:center;">
              Automated message from <strong>Audit Tools</strong> &mdash; please do not reply.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
