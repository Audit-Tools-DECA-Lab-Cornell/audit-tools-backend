"""Consistent, email-client-safe HTML templates for transactional email.

The templates in this module intentionally use table-based layout and inline
styles because major email clients still have uneven CSS support. Shared render
helpers keep branding, spacing, buttons, tracking, accessibility, dark mode, and
footer language consistent across every transactional email.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_WEB_APP_URL = "https://audit-tools-playspace-frontend.vercel.app/"
_IOS_APP_URL = "https://apps.apple.com/app/id6755903317"
_ANDROID_APP_URL = "https://play.google.com/apps/internaltest/4701144847649057394"

_BRAND_NAME = "Audit Tools"
_DEFAULT_PLATFORM = "Audit Tools"
_LOGO_URL = "https://audit-tools-playspace-frontend.vercel.app/icon.png"

# Email clients are inconsistent. Keep the core layout inline, then use this
# style block only for progressive enhancement: normalization, dark mode, and
# mobile behavior. Gmail web may strip this block; the inline layout still holds.
_COLOR_SCHEME_META = """\
  <meta name="color-scheme" content="light dark" />
  <meta name="supported-color-schemes" content="light dark" />"""

_STYLE_BLOCK = """\
<style>
  /* Client normalization */
  body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
  table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
  table { border-collapse: collapse !important; }
  img { border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }
  body { margin: 0 !important; padding: 0 !important; width: 100% !important; }
  a[x-apple-data-detectors] { color: inherit !important; text-decoration: none !important; font-size: inherit !important; }
  .x-gmail-data-detectors, .x-gmail-data-detectors * { color: inherit !important; text-decoration: none !important; }

  /* Prevent hidden preheader text from leaking into body rendering */
  .preheader { display: none !important; visibility: hidden; opacity: 0; color: transparent; height: 0; width: 0; overflow: hidden; mso-hide: all; }

  /* Dark mode */
  @media (prefers-color-scheme: dark) {
    .email-bg      { background-color: #1a120a !important; }
    .email-card    { background-color: #2d1f14 !important; }
    .email-text    { color: #f0e4d4 !important; }
    .email-muted   { color: #c4a886 !important; }
    .email-label   { color: #e8c99a !important; }
    .email-divider { border-top-color: #4a3020 !important; }
    .panel-card    { background-color: #3a2410 !important; border-left-color: #e8c99a !important; }
    .panel-row td  { border-bottom-color: #4a3020 !important; }
    .notice-cell   { background-color: #3a2400 !important; }
    .notice-text   { color: #f5c876 !important; }
    .btn-outline   { color: #e8c99a !important; border-color: #e8c99a !important; background-color: #2d1f14 !important; }
    .step-text     { color: #f0e4d4 !important; }
    .step-num      { color: #e8c99a !important; }
  }

  /* Mobile */
  @media only screen and (max-width: 600px) {
    .email-card    { width: 100% !important; border-radius: 0 !important; }
    .email-outer   { padding: 0 !important; }
    .email-header  { padding: 28px 20px 22px 20px !important; }
    .email-content { padding-left: 20px !important; padding-right: 20px !important; }
    .email-footer  { padding: 24px 20px 30px 20px !important; }
    .email-title   { font-size: 24px !important; }
    .app-cell      { display: block !important; width: 100% !important; padding: 0 0 8px 0 !important; }
    .btn-primary, .btn-outline { box-sizing: border-box !important; width: 100% !important; text-align: center !important; }
    .field-label   { display: block !important; width: 100% !important; padding-bottom: 4px !important; }
    .field-value   { display: block !important; width: 100% !important; }
  }
</style>"""


@dataclass(frozen=True)
class EmailCta:
	label: str
	url: str
	content_tag: str
	aria_label: str | None = None


@dataclass(frozen=True)
class EmailPanelRow:
	label: str
	value: str
	is_code: bool = False


def _h(value: object) -> str:
	"""Escape dynamic values before injecting them into HTML body content."""
	return escape(str(value), quote=True)


def _href(url: str) -> str:
	"""Escape dynamic URL values before injecting them into href attributes."""
	return escape(str(url), quote=True)


def _add_utm_params(
	url: str,
	*,
	campaign: str,
	content: str,
	source: str = "email",
	medium: str = "transactional",
) -> str:
	"""Append UTM parameters to a URL while preserving existing query parameters."""
	parsed = urlparse(url)
	existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
	merged = {
		**existing,
		"utm_source": source,
		"utm_medium": medium,
		"utm_campaign": campaign,
		"utm_content": content,
	}
	return urlunparse(parsed._replace(query=urlencode(merged)))


def _tracked(cta: EmailCta, *, campaign: str) -> str:
	return _add_utm_params(cta.url, campaign=campaign, content=cta.content_tag)


def _head(*, title: str) -> str:
	return f"""\
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="x-apple-disable-message-reformatting" />
{_COLOR_SCHEME_META}
  <title>{_h(title)}</title>
  {_STYLE_BLOCK}
</head>"""


def _preheader(text: str) -> str:
	# Extra invisible characters help prevent inboxes from pulling unrelated body
	# copy into the preview line after the intended preheader.
	spacer = "&zwnj;&nbsp;" * 24
	return f"""\
  <span class="preheader" style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;mso-hide:all;">
    {_h(text)} {spacer}
  </span>"""


def _header(*, eyebrow: str, heading: str, product: str | None = None, show_logo: bool = True) -> str:
	logo_html = ""
	if show_logo:
		logo_alt = f"{product or _BRAND_NAME} logo"
		logo_html = f"""
            <img src="{_href(_LOGO_URL)}" alt="{_h(logo_alt)}" width="64" height="64"
              style="display:block;margin:0 auto 16px auto;border-radius:14px;" />"""

	return f"""\
        <tr>
          <td class="email-header" style="background-color:#7a4f2e;padding:36px 40px 28px 40px;text-align:center;">
{logo_html}
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#e8c99a;font-family:Arial,Helvetica,sans-serif;">
              {_h(eyebrow)}
            </p>
            <h1 class="email-title" style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;font-family:Georgia,'Times New Roman',serif;">
              {_h(heading)}
            </h1>
          </td>
        </tr>"""


def _paragraph(html: str, *, margin: str = "0 0 18px 0", muted: bool = False, align: str = "left") -> str:
	"""Render a paragraph. ``html`` may contain trusted markup (e.g. <strong>);
	any dynamic values it embeds must already be escaped by the caller via _h()."""
	class_name = "email-muted" if muted else "email-text"
	color = "#7a6050" if muted else "#3d2a1a"
	return f"""\
            <p class="{class_name}" style="margin:{margin};font-size:15px;color:{color};line-height:1.7;text-align:{align};">
              {html}
            </p>"""


def _section_label(label: str, *, margin: str = "0 0 14px 0") -> str:
	return f"""\
            <p class="email-label" style="margin:{margin};font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;font-family:Arial,Helvetica,sans-serif;font-weight:700;">
              {_h(label)}
            </p>"""


def _primary_button(cta: EmailCta, *, campaign: str, full_width: bool = False) -> str:
	url = _tracked(cta, campaign=campaign)
	width_style = "width:100%;" if full_width else "margin:0 auto;"
	display_style = "display:block;text-align:center;" if full_width else "display:inline-block;"
	aria_label = cta.aria_label or cta.label
	return f"""\
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="{width_style}">
              <tr>
                <td style="border-radius:8px;background-color:#7a4f2e;">
                  <a href="{_href(url)}" class="btn-primary" aria-label="{_h(aria_label)}"
                    style="{display_style}padding:14px 28px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">
                    {_h(cta.label)} &rarr;
                  </a>
                </td>
              </tr>
            </table>"""


def _outline_button(cta: EmailCta, *, campaign: str) -> str:
	url = _tracked(cta, campaign=campaign)
	aria_label = cta.aria_label or cta.label
	return f"""\
                  <a href="{_href(url)}" class="btn-outline" aria-label="{_h(aria_label)}"
                    style="display:block;padding:12px 8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#7a4f2e;text-decoration:none;border-radius:8px;text-align:center;border:2px solid #7a4f2e;background-color:#ffffff;">
                    {_h(cta.label)} &rarr;
                  </a>"""


# Single canonical fallback-link intro used across all templates.
_FALLBACK_LINK_INTRO = "If the button above does not work, copy and paste this link into your browser:"


def _fallback_link(url: str, *, campaign: str, content: str) -> str:
	"""Render a plain-text fallback URL below the primary CTA button.

	The intro line is intentionally fixed so all templates read identically.
	"""
	tracked_url = _add_utm_params(url, campaign=campaign, content=content)
	return f"""\
            <p class="email-muted" style="margin:20px 0 0 0;font-size:12px;color:#9a7050;text-align:center;line-height:1.6;">
              {_h(_FALLBACK_LINK_INTRO)}<br />
              <a href="{_href(tracked_url)}" style="color:#7a4f2e;word-break:break-all;text-decoration:underline;">{_h(url)}</a>
            </p>"""


def _notice(message_html: str) -> str:
	return f"""\
        <tr>
          <td class="email-content" style="padding:20px 40px 0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-radius:6px;border-left:4px solid #c8860a;">
              <tr>
                <td class="notice-cell" style="padding:14px 18px;background-color:#fff8e6;border-radius:6px;">
                  <p class="notice-text" style="margin:0;font-size:13px;color:#7a5000;line-height:1.6;">
                    {message_html}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""


def _panel(title: str, rows: list[EmailPanelRow]) -> str:
	row_html: list[str] = []
	for index, row in enumerate(rows):
		is_last = index == len(rows) - 1
		border = "" if is_last else "border-bottom:1px solid #e8d5bf;"
		value_style = (
			"font-family:'Courier New',Courier,monospace;font-size:17px;color:#3d2a1a;"
			"font-weight:700;letter-spacing:2px;"
			if row.is_code
			else "font-size:15px;color:#3d2a1a;font-weight:600;"
		)
		row_html.append(
			f"""\
                  <tr class="panel-row">
                    <td style="padding:12px 0;{border}">
                      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                        <tr>
                          <td class="email-label field-label" style="font-size:12px;color:#9a7050;text-transform:uppercase;letter-spacing:1px;width:145px;font-family:Arial,Helvetica,sans-serif;font-weight:700;vertical-align:top;">
                            {_h(row.label)}
                          </td>
                          <td class="email-text field-value" style="{value_style}vertical-align:top;word-break:break-word;">
                            {_h(row.value)}
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>"""
		)

	return f"""\
        <tr>
          <td class="email-content" style="padding:0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
              class="panel-card" style="background-color:#fdf6ee;border-radius:8px;border-left:4px solid #7a4f2e;">
              <tr>
                <td style="padding:22px 26px;">
                  <p class="email-label" style="margin:0 0 4px 0;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#9a7050;font-family:Arial,Helvetica,sans-serif;font-weight:700;">
                    {_h(title)}
                  </p>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:8px;">
{"".join(row_html)}
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""


def _steps(title: str, steps: list[str]) -> str:
	items = []
	for index, step in enumerate(steps, start=1):
		bottom_padding = "4px" if index == len(steps) else "10px"
		items.append(
			f"""\
              <tr>
                <td class="step-num" style="width:24px;vertical-align:top;padding:4px 0;font-size:14px;font-weight:700;color:#7a4f2e;font-family:Arial,Helvetica,sans-serif;">{index}.</td>
                <td class="step-text" style="vertical-align:top;padding:4px 0 {bottom_padding} 10px;font-size:14px;color:#3d2a1a;line-height:1.6;">{step}</td>
              </tr>"""
		)
	return f"""\
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label(title)}
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
{"".join(items)}
            </table>
          </td>
        </tr>"""


def _app_links(*, role: str, campaign: str) -> str:
	if role == "manager":
		return ""
	ios = EmailCta("iOS App Store", _IOS_APP_URL, "ios_app", "Open the iOS App Store listing")
	android = EmailCta("Android App", _ANDROID_APP_URL, "android_app", "Open the Android app listing")
	return f"""\
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-top:12px;">
              <tr>
                <td class="app-cell" style="width:49%;padding-right:6px;">
{_outline_button(ios, campaign=campaign)}
                </td>
                <td class="app-cell" style="width:49%;padding-left:6px;">
{_outline_button(android, campaign=campaign)}
                </td>
              </tr>
            </table>"""


def _footer(*, platform: str, expectation_note: str) -> str:
	return f"""\
        <tr>
          <td class="email-footer" style="padding:28px 40px 36px 40px;">
            <hr class="email-divider" style="border:none;border-top:1px solid #e8d5bf;margin:0 0 24px 0;" />
            <p class="email-muted" style="margin:0 0 16px 0;font-size:13px;color:#7a6050;line-height:1.7;text-align:left;">
              {expectation_note}
            </p>
            <p class="email-muted" style="margin:0;font-size:12px;color:#b09070;text-align:center;line-height:1.8;">
              This is an automated message from <strong>{_h(platform)}</strong>.<br />
              Please do not reply directly to this email.
            </p>
          </td>
        </tr>"""


def _render_email(
	*,
	title: str,
	preheader: str,
	eyebrow: str,
	heading: str,
	body_rows: str,
	platform: str = _DEFAULT_PLATFORM,
	product: str | None = None,
	footer_note: str,
) -> str:
	return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
{_head(title=title)}
<body class="email-bg" style="margin:0;padding:0;background-color:#f5ede0;font-family:Arial,Helvetica,sans-serif;">
{_preheader(preheader)}
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="email-bg email-outer" style="background-color:#f5ede0;padding:40px 0;">
    <tr>
      <td align="center" style="padding:0;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="email-card" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(80,50,20,0.10);">
{_header(eyebrow=eyebrow, heading=heading, product=product, show_logo=True)}
{body_rows}
{_footer(platform=platform, expectation_note=footer_note)}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def credentials_html(
	full_name: str,
	to_email: str,
	auditor_code: str,
	temporary_password: str,
	platform: str,
	product: str,
) -> str:
	"""Render the auditor temporary-credentials email."""
	campaign = "auditor_credentials"
	web_cta = EmailCta(
		"Open Web Dashboard",
		_WEB_APP_URL,
		"web_dashboard_cta",
		"Open the web dashboard and sign in",
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(full_name)}</strong>,", margin="0 0 20px 0")}
{
		_paragraph(
			f"A {_h(product)} auditor account has been created for you by your workspace manager. Your temporary login credentials are below. Treat this information as confidential and update your password after your first sign-in.",
			margin="0 0 28px 0",
		)
	}
          </td>
        </tr>
{
		_panel(
			"Login Credentials",
			[
				EmailPanelRow("Email", to_email),
				EmailPanelRow("Auditor Code", auditor_code, is_code=True),
				EmailPanelRow("Temporary Password", temporary_password, is_code=True),
			],
		)
	}
{
		_notice(
			"&#9888;&#65039; <strong>Action required:</strong> Sign in and change your temporary password immediately. Temporary credentials are short-lived and should never be shared."
		)
	}
{
		_steps(
			"Getting Started",
			[
				"Sign in using the credentials above through the web dashboard or mobile app.",
				"Go to <strong>Account Settings</strong> and update your password immediately.",
				"Start your assigned audits from the <strong>My Assignments</strong> dashboard.",
			],
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Sign In & Reset Your Password", margin="0 0 16px 0")}
{_primary_button(web_cta, campaign=campaign, full_width=True)}
{_app_links(role="auditor", campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		title=f"Your {product} Auditor Account",
		preheader=f"Your {product} auditor credentials are ready. Sign in and update your password immediately.",
		eyebrow=platform,
		heading="Your Auditor Account Is Ready",
		body_rows=body_rows,
		platform=platform,
		product=product,
		footer_note="If you were not expecting this account or believe it was created in error, contact your workspace administrator immediately and disregard this message.",
	)


def invite_html(
	invite_url: str,
	role: str,
	*,
	organization_name: str | None = None,
	invited_by_name: str | None = None,
) -> str:
	"""Render the workspace invitation email for managers and auditors.

	For manager invites, pass ``organization_name`` and ``invited_by_name`` so
	the recipient knows which workspace they are joining and who invited them.
	"""
	role_label = "manager" if role == "manager" else "auditor"
	is_manager = role_label == "manager"
	campaign = "manager_invite" if is_manager else "auditor_invite"

	invite_cta = EmailCta(
		"Accept Invitation",
		invite_url,
		"accept_invitation_cta",
		f"Accept your {_BRAND_NAME} invitation",
	)

	# Workspace panel: shown for managers (always) and auditors when org info
	# is available, so recipients always know which workspace they are joining.
	panel_rows: list[EmailPanelRow] = []
	if organization_name:
		panel_rows.append(EmailPanelRow("Organisation", organization_name))
	if invited_by_name:
		panel_rows.append(EmailPanelRow("Invited by", invited_by_name))
	workspace_panel = _panel("Workspace Details", panel_rows) if panel_rows else ""

	# Role-specific copy
	if is_manager:
		role_action = "set your password and configure your workspace"
		steps_title = "Getting Started as a Manager"
		steps_list = [
			"Click <strong>Accept Invitation</strong> below to create your account.",
			"Set a strong password and complete your manager profile.",
			"Configure your workspace settings and invite your first auditors from the <strong>Team</strong> tab.",
		]
		notice_msg = "&#9888;&#65039; <strong>This invitation is personal:</strong> the link is tied to your email address. Do not share it. It expires in <strong>7 days</strong>."
		cta_label = "Accept Your Invitation"
	else:
		role_action = "create your account and start auditing"
		steps_title = "Getting Started as an Auditor"
		steps_list = [
			"Click <strong>Accept Invitation</strong> below to create your account.",
			"Set a strong password when prompted.",
			"Your assigned audits will appear on the <strong>My Assignments</strong> dashboard.",
		]
		notice_msg = "&#9888;&#65039; <strong>This invitation is personal:</strong> the link is tied to your email address. Do not share it. It expires in <strong>7 days</strong>."
		cta_label = "Accept Your Invitation"

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"You have been invited to join an {_h(_BRAND_NAME)} workspace as a <strong>{_h(role_label)}</strong>.", margin="0 0 16px 0")}
{_paragraph(f"Use the button below to {_h(role_action)}. Once you accept, you will be guided through a short setup flow.", margin="0 0 28px 0")}
          </td>
        </tr>
{workspace_panel}
{_notice(notice_msg)}
{_steps(steps_title, steps_list)}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label(cta_label, margin="0 0 16px 0")}
{_primary_button(invite_cta, campaign=campaign, full_width=True)}
{_fallback_link(invite_url, campaign=campaign, content="plain_link")}
{_app_links(role=role, campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		title=f"You have been invited to {_BRAND_NAME}",
		preheader=f"You have been invited to join an {_BRAND_NAME} workspace as a {role_label}. Accept your invite to get started.",
		eyebrow=_BRAND_NAME,
		heading="You Have Been Invited",
		body_rows=body_rows,
		platform=_BRAND_NAME,
		product=_BRAND_NAME,
		footer_note="If you were not expecting this invitation, you can safely ignore this email. No account will be created without your action.",
	)


def submit_failure_html(
	auditor_name: str,
	place_name: str,
	audit_code: str,
	project_name: str,
) -> str:
	"""Render the background audit submit-failure notification email."""
	campaign = "audit_submit_failure"

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(auditor_name)}</strong>,", margin="0 0 20px 0")}
{
		_paragraph(
			"We were unable to automatically submit your audit after it was queued while your device was offline. "
			"The audit data is safely saved on your device. Please open the Playspace app and resubmit manually.",
			margin="0 0 28px 0",
		)
	}
          </td>
        </tr>
{
		_panel(
			"Audit Details",
			[
				EmailPanelRow("Place", place_name),
				EmailPanelRow("Project", project_name),
				EmailPanelRow("Audit Code", audit_code, is_code=True),
			],
		)
	}
{
		_notice(
			"&#9888;&#65039; <strong>Action required:</strong> Open the Playspace app and submit your audit manually. "
			"Your recorded responses are still on your device and have not been lost."
		)
	}
{
		_steps(
			"How to Resubmit",
			[
				"Open the <strong>Playspace</strong> app on your device.",
				"Navigate to the audit listed above in your <strong>In Progress</strong> audits.",
				"Tap <strong>Submit</strong> to complete the submission.",
				"Contact your manager if the problem persists.",
			],
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Open the App", margin="0 0 16px 0")}
{_app_links(role="auditor", campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		title="Audit Submission Failed",
		preheader=f"Your audit for {place_name} could not be submitted automatically. Open the app to resubmit.",
		eyebrow="Playspace",
		heading="Your Audit Could Not Be Submitted",
		body_rows=body_rows,
		platform="Playspace",
		product="Playspace",
		footer_note="You received this message because your Playspace account had an audit queued for offline submission that could not be processed automatically.",
	)


def export_ready_html(
	*,
	requester_name: str,
	entity_label: str,
	format_label: str,
	audit_count: int,
	combined_report_count: int,
	dashboard_url: str,
	had_failures: bool,
) -> str:
	"""Render the raw-data export completion email.

	The export ZIP is generated in the requester's browser and downloads there
	directly, so this email confirms completion rather than carrying a download
	link. The call-to-action returns the user to the raw-data dashboard.
	"""
	campaign = "raw_data_export_ready"

	panel_rows = [
		EmailPanelRow("Export", entity_label),
		EmailPanelRow("Format", format_label),
		EmailPanelRow("Audits", str(audit_count)),
	]
	if combined_report_count > 0:
		panel_rows.append(EmailPanelRow("Combined reports", str(combined_report_count)))

	failure_notice = (
		_notice(
			"&#9888;&#65039; <strong>Some items were skipped.</strong> A few audits or reports could not be "
			"included - see <strong>manifest.json</strong> inside the ZIP for the full list and reasons."
		)
		if had_failures
		else ""
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(requester_name)}</strong>,", margin="0 0 20px 0")}
{
		_paragraph(
			"Your raw-data export has finished building and the ZIP has downloaded in your browser. "
			"Each audit and combined report is included as a PDF alongside the data file you chose.",
			margin="0 0 28px 0",
		)
	}
          </td>
        </tr>
{_panel("Export Summary", panel_rows)}
{failure_notice}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Back to the dashboard", margin="0 0 16px 0")}
{
		_primary_button(
			EmailCta(
				label="Open raw data",
				url=dashboard_url,
				content_tag="export_ready_cta",
				aria_label="Open the raw data dashboard",
			),
			campaign=campaign,
		)
	}
{
		_fallback_link(
			dashboard_url,
			campaign=campaign,
			content="export_ready_fallback",
		)
	}
          </td>
        </tr>"""

	return _render_email(
		title="Your Export Is Ready",
		preheader=f"Your {entity_label} export finished and downloaded in your browser.",
		eyebrow="Playspace",
		heading="Your Export Is Ready",
		body_rows=body_rows,
		platform="Playspace",
		product="Playspace",
		footer_note="You received this message because you requested a raw-data export from the Playspace dashboard.",
	)


def verification_html(verify_url: str) -> str:
	"""Render the account email-verification email."""
	campaign = "email_verification"
	verify_cta = EmailCta(
		"Verify My Email",
		verify_url,
		"verify_cta",
		"Verify your email address",
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Welcome to {_h(_BRAND_NAME)}! You are one step away from activating your account.", margin="0 0 16px 0")}
{
		_paragraph(
			"Confirm your email address using the button below. This ensures your account is secure and that you receive important notifications.",
			margin="0 0 0 0",
		)
	}
          </td>
        </tr>
{
		_notice(
			"&#9888;&#65039; <strong>This link expires in 24 hours.</strong> If it expires before you use it, sign in to request a new verification link."
		)
	}
{
		_steps(
			"What Happens Next",
			[
				"Click <strong>Verify My Email</strong> below to confirm your address.",
				"Your account will be activated immediately - no further steps needed.",
				"You can then sign in and access your assigned workspace.",
			],
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Verify Your Email Address", margin="0 0 16px 0")}
{_primary_button(verify_cta, campaign=campaign, full_width=True)}
{_fallback_link(verify_url, campaign=campaign, content="plain_link")}
{_app_links(role="auditor", campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		title=f"Verify Your {_BRAND_NAME} Account",
		preheader=f"One more step: verify your email to activate your {_BRAND_NAME} account.",
		eyebrow=_BRAND_NAME,
		heading="Verify Your Email Address",
		body_rows=body_rows,
		platform=_BRAND_NAME,
		product=_BRAND_NAME,
		footer_note=f"If you did not create an {_BRAND_NAME} account, you can safely ignore this email.",
	)
