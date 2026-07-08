"""Consistent, email-client-safe HTML templates for transactional email.

The templates in this module intentionally use table-based layout and inline
styles because major email clients still have uneven CSS support. Shared render
helpers keep branding, spacing, buttons, tracking, accessibility, dark mode, and
footer language consistent across every transactional email.

Design is product-scoped through :class:`EmailTheme`. Each product carries its
own palette, typography, brand assets, and dark-mode treatment so that a YEE
email looks like YEE (deep-green, Inter, cool surfaces) and a Playspace email
looks like Playspace (earthy brown, serif headings, warm surfaces) even though
both flow through the same render helpers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# ---------------------------------------------------------------------------
# Theme (per-product design system)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmailTheme:
	"""A product's email design system.

	Colours are plain hex because email clients do not support OKLCH; the YEE
	values below are the sRGB conversions of the OKLCH brand tokens defined in
	the ``yee-frontend`` design system (``src/app/globals.css`` / ``DESIGN.md``).
	"""

	key: str
	brand_name: str

	# Typography
	font_body: str
	font_heading: str

	# Light palette
	bg_outer: str
	card: str
	shadow: str
	header_bg: str
	eyebrow: str
	heading: str
	text: str
	text_muted: str
	label: str
	btn_bg: str
	btn_text: str
	link: str
	outline_text: str
	outline_border: str
	outline_bg: str
	notice_border: str
	notice_bg: str
	notice_text: str
	panel_bg: str
	panel_border: str
	panel_divider: str
	step_num: str
	step_text: str
	footer_divider: str
	footer_muted: str
	footer_brand: str

	# Dark palette (progressive enhancement via prefers-color-scheme)
	dark_bg: str
	dark_card: str
	dark_text: str
	dark_muted: str
	dark_label: str
	dark_divider: str
	dark_panel_bg: str
	dark_panel_border: str
	dark_notice_bg: str
	dark_notice_text: str
	dark_outline_text: str
	dark_outline_border: str
	dark_outline_bg: str
	dark_step_text: str
	dark_step_num: str

	# Brand assets / destinations
	logo_url: str | None
	web_app_url: str
	ios_app_url: str | None
	android_app_url: str | None


# YEE brand assets are configurable so deployments can point at a hosted logo
# without a code change; the default omits the logo image (rendering a text
# wordmark header) rather than shipping a broken <img>.
_YEE_LOGO_URL = os.getenv("YEE_EMAIL_LOGO_URL", "").strip() or None
_PLAYSPACE_LOGO_URL = os.getenv("PLAYSPACE_EMAIL_LOGO_URL", "https://copa-tool.vercel.app/icon.png").strip() or None

_SANS_STACK = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,Helvetica,sans-serif"


# YEE — deep forest green, cool near-white surfaces, Inter (sans) throughout.
# Hex values are sRGB conversions of the OKLCH brand tokens in yee-frontend.
YEE_THEME = EmailTheme(
	key="yee",
	brand_name="YEE Audit Tools",
	font_body=_SANS_STACK,
	font_heading=_SANS_STACK,
	bg_outer="#f0f7f2",  # green-50 wash
	card="#ffffff",
	shadow="0 4px 20px rgba(15,48,33,0.10)",
	header_bg="#0f3021",  # green-800 (deep brand green)
	eyebrow="#b6d3c1",  # green-200
	heading="#ffffff",
	text="#1b2a22",
	text_muted="#5c6b64",
	label="#3d7055",  # green-600
	btn_bg="#0f3021",
	btn_text="#ffffff",
	link="#224c37",  # green-700
	outline_text="#0f3021",
	outline_border="#0f3021",
	outline_bg="#ffffff",
	notice_border="#b18c39",  # score-mid gold
	notice_bg="#f9edd5",  # score-mid-bg
	notice_text="#5b4315",  # amenities-text ochre
	panel_bg="#f0f7f2",  # green-50
	panel_border="#0f3021",
	panel_divider="#d7e6dd",
	step_num="#3d7055",
	step_text="#1b2a22",
	footer_divider="#d7e6dd",
	footer_muted="#5c6b64",
	footer_brand="#8aa596",
	dark_bg="#0a1512",
	dark_card="#10201a",
	dark_text="#e6efe9",
	dark_muted="#9fb3a8",
	dark_label="#7fb79b",
	dark_divider="#24382e",
	dark_panel_bg="#16281f",
	dark_panel_border="#7fb79b",
	dark_notice_bg="#2e2410",
	dark_notice_text="#f5c876",
	dark_outline_text="#9fd0b8",
	dark_outline_border="#9fd0b8",
	dark_outline_bg="#10201a",
	dark_step_text="#e6efe9",
	dark_step_num="#9fd0b8",
	logo_url=_YEE_LOGO_URL,
	web_app_url="https://yee-audit-tools.vercel.app/",
	ios_app_url=None,  # No dedicated YEE App Store listing yet.
	android_app_url="https://play.google.com/store/apps/details?id=com.andisha2004.audittoolsyeemobile",
)


# Playspace / COPA — earthy brown, warm surfaces, serif (Georgia) headings.
# Preserves the original design system exactly.
PLAYSPACE_THEME = EmailTheme(
	key="playspace",
	brand_name="Playspace Audit Tools",
	font_body="Arial,Helvetica,sans-serif",
	font_heading="Georgia,'Times New Roman',serif",
	bg_outer="#f5ede0",
	card="#ffffff",
	shadow="0 4px 20px rgba(80,50,20,0.10)",
	header_bg="#7a4f2e",
	eyebrow="#e8c99a",
	heading="#ffffff",
	text="#3d2a1a",
	text_muted="#7a6050",
	label="#9a7050",
	btn_bg="#7a4f2e",
	btn_text="#ffffff",
	link="#7a4f2e",
	outline_text="#7a4f2e",
	outline_border="#7a4f2e",
	outline_bg="#ffffff",
	notice_border="#c8860a",
	notice_bg="#fff8e6",
	notice_text="#7a5000",
	panel_bg="#fdf6ee",
	panel_border="#7a4f2e",
	panel_divider="#e8d5bf",
	step_num="#7a4f2e",
	step_text="#3d2a1a",
	footer_divider="#e8d5bf",
	footer_muted="#7a6050",
	footer_brand="#b09070",
	dark_bg="#1a120a",
	dark_card="#2d1f14",
	dark_text="#f0e4d4",
	dark_muted="#c4a886",
	dark_label="#e8c99a",
	dark_divider="#4a3020",
	dark_panel_bg="#3a2410",
	dark_panel_border="#e8c99a",
	dark_notice_bg="#3a2400",
	dark_notice_text="#f5c876",
	dark_outline_text="#e8c99a",
	dark_outline_border="#e8c99a",
	dark_outline_bg="#2d1f14",
	dark_step_text="#f0e4d4",
	dark_step_num="#e8c99a",
	logo_url=_PLAYSPACE_LOGO_URL,
	web_app_url="https://copa-tool.vercel.app/",
	ios_app_url="https://apps.apple.com/app/id6755903317",
	android_app_url="https://play.google.com/apps/internaltest/4701144847649057394",
)


_THEMES_BY_PRODUCT: dict[str, EmailTheme] = {
	"yee": YEE_THEME,
	"playspace": PLAYSPACE_THEME,
}


def theme_for_product(product: str) -> EmailTheme:
	"""Resolve the email theme for a product key, defaulting to YEE."""
	return _THEMES_BY_PRODUCT.get(product.strip().lower(), YEE_THEME)


_COLOR_SCHEME_META = """\
  <meta name="color-scheme" content="light dark" />
  <meta name="supported-color-schemes" content="light dark" />"""


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


def _style_block(theme: EmailTheme) -> str:
	"""Build the progressive-enhancement style block for a theme.

	Email clients are inconsistent. Keep the core layout inline, then use this
	block only for progressive enhancement: normalization, dark mode, and mobile
	behavior. Gmail web may strip this block; the inline layout still holds.
	"""
	return f"""\
<style>
  /* Client normalization */
  body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  table {{ border-collapse: collapse !important; }}
  img {{ border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }}
  body {{ margin: 0 !important; padding: 0 !important; width: 100% !important; }}
  a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: none !important; font-size: inherit !important; }}
  .x-gmail-data-detectors, .x-gmail-data-detectors * {{ color: inherit !important; text-decoration: none !important; }}

  /* Prevent hidden preheader text from leaking into body rendering */
  .preheader {{ display: none !important; visibility: hidden; opacity: 0; color: transparent; height: 0; width: 0; overflow: hidden; mso-hide: all; }}

  /* Dark mode */
  @media (prefers-color-scheme: dark) {{
    .email-bg      {{ background-color: {theme.dark_bg} !important; }}
    .email-card    {{ background-color: {theme.dark_card} !important; }}
    .email-text    {{ color: {theme.dark_text} !important; }}
    .email-muted   {{ color: {theme.dark_muted} !important; }}
    .email-label   {{ color: {theme.dark_label} !important; }}
    .email-divider {{ border-top-color: {theme.dark_divider} !important; }}
    .panel-card    {{ background-color: {theme.dark_panel_bg} !important; border-left-color: {theme.dark_panel_border} !important; }}
    .panel-row td  {{ border-bottom-color: {theme.dark_divider} !important; }}
    .notice-cell   {{ background-color: {theme.dark_notice_bg} !important; }}
    .notice-text   {{ color: {theme.dark_notice_text} !important; }}
    .btn-outline   {{ color: {theme.dark_outline_text} !important; border-color: {theme.dark_outline_border} !important; background-color: {theme.dark_outline_bg} !important; }}
    .step-text     {{ color: {theme.dark_step_text} !important; }}
    .step-num      {{ color: {theme.dark_step_num} !important; }}
  }}

  /* Mobile */
  @media only screen and (max-width: 600px) {{
    .email-card    {{ width: 100% !important; border-radius: 0 !important; }}
    .email-outer   {{ padding: 0 !important; }}
    .email-header  {{ padding: 28px 20px 22px 20px !important; }}
    .email-content {{ padding-left: 20px !important; padding-right: 20px !important; }}
    .email-footer  {{ padding: 24px 20px 30px 20px !important; }}
    .email-title   {{ font-size: 24px !important; }}
    .app-cell      {{ display: block !important; width: 100% !important; padding: 0 0 8px 0 !important; }}
    .btn-primary, .btn-outline {{ box-sizing: border-box !important; width: 100% !important; text-align: center !important; }}
    .field-label   {{ display: block !important; width: 100% !important; padding-bottom: 4px !important; }}
    .field-value   {{ display: block !important; width: 100% !important; }}
  }}
</style>"""


def _head(*, title: str, theme: EmailTheme) -> str:
	return f"""\
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="x-apple-disable-message-reformatting" />
{_COLOR_SCHEME_META}
  <title>{_h(title)}</title>
  {_style_block(theme)}
</head>"""


def _preheader(text: str) -> str:
	# Extra invisible characters help prevent inboxes from pulling unrelated body
	# copy into the preview line after the intended preheader.
	spacer = "&zwnj;&nbsp;" * 24
	return f"""\
  <span class="preheader" style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;mso-hide:all;">
    {_h(text)} {spacer}
  </span>"""


def _header(*, eyebrow: str, heading: str, theme: EmailTheme) -> str:
	logo_html = ""
	if theme.logo_url:
		logo_alt = f"{theme.brand_name} logo"
		logo_html = f"""
            <img src="{_href(theme.logo_url)}" alt="{_h(logo_alt)}" width="64" height="64"
              style="display:block;margin:0 auto 16px auto;border-radius:14px;" />"""

	return f"""\
        <tr>
          <td class="email-header" style="background-color:{theme.header_bg};padding:36px 40px 28px 40px;text-align:center;">
{logo_html}
            <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:{theme.eyebrow};font-family:{theme.font_body};">
              {_h(eyebrow)}
            </p>
            <h1 class="email-title" style="margin:0;font-size:26px;font-weight:700;color:{theme.heading};line-height:1.3;font-family:{theme.font_heading};">
              {_h(heading)}
            </h1>
          </td>
        </tr>"""


def _paragraph(
	html: str, *, theme: EmailTheme, margin: str = "0 0 18px 0", muted: bool = False, align: str = "left"
) -> str:
	"""Render a paragraph. ``html`` may contain trusted markup (e.g. <strong>);
	any dynamic values it embeds must already be escaped by the caller via _h()."""
	class_name = "email-muted" if muted else "email-text"
	color = theme.text_muted if muted else theme.text
	return f"""\
            <p class="{class_name}" style="margin:{margin};font-size:15px;color:{color};line-height:1.7;text-align:{align};font-family:{theme.font_body};">
              {html}
            </p>"""


def _section_label(label: str, *, theme: EmailTheme, margin: str = "0 0 14px 0") -> str:
	return f"""\
            <p class="email-label" style="margin:{margin};font-size:13px;letter-spacing:2px;text-transform:uppercase;color:{theme.label};font-family:{theme.font_body};font-weight:700;">
              {_h(label)}
            </p>"""


def _primary_button(cta: EmailCta, *, theme: EmailTheme, campaign: str, full_width: bool = False) -> str:
	url = _tracked(cta, campaign=campaign)
	width_style = "width:100%;" if full_width else "margin:0 auto;"
	display_style = "display:block;text-align:center;" if full_width else "display:inline-block;"
	aria_label = cta.aria_label or cta.label
	return f"""\
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="{width_style}">
              <tr>
                <td style="border-radius:8px;background-color:{theme.btn_bg};">
                  <a href="{_href(url)}" class="btn-primary" aria-label="{_h(aria_label)}"
                    style="{display_style}padding:14px 28px;font-family:{theme.font_body};font-size:15px;font-weight:700;color:{theme.btn_text};text-decoration:none;border-radius:8px;">
                    {_h(cta.label)} &rarr;
                  </a>
                </td>
              </tr>
            </table>"""


def _outline_button(cta: EmailCta, *, theme: EmailTheme, campaign: str) -> str:
	url = _tracked(cta, campaign=campaign)
	aria_label = cta.aria_label or cta.label
	return f"""\
                  <a href="{_href(url)}" class="btn-outline" aria-label="{_h(aria_label)}"
                    style="display:block;padding:12px 8px;font-family:{theme.font_body};font-size:13px;font-weight:700;color:{theme.outline_text};text-decoration:none;border-radius:8px;text-align:center;border:2px solid {theme.outline_border};background-color:{theme.outline_bg};">
                    {_h(cta.label)} &rarr;
                  </a>"""


# Single canonical fallback-link intro used across all templates.
_FALLBACK_LINK_INTRO = "If the button above does not work, copy and paste this link into your browser:"


def _fallback_link(url: str, *, theme: EmailTheme, campaign: str, content: str) -> str:
	"""Render a plain-text fallback URL below the primary CTA button.

	The intro line is intentionally fixed so all templates read identically.
	"""
	tracked_url = _add_utm_params(url, campaign=campaign, content=content)
	return f"""\
            <p class="email-muted" style="margin:20px 0 0 0;font-size:12px;color:{theme.text_muted};text-align:center;line-height:1.6;font-family:{theme.font_body};">
              {_h(_FALLBACK_LINK_INTRO)}<br />
              <a href="{_href(tracked_url)}" style="color:{theme.link};word-break:break-all;text-decoration:underline;">{_h(url)}</a>
            </p>"""


def _notice(message_html: str, *, theme: EmailTheme) -> str:
	return f"""\
        <tr>
          <td class="email-content" style="padding:20px 40px 0 40px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-radius:6px;border-left:4px solid {theme.notice_border};">
              <tr>
                <td class="notice-cell" style="padding:14px 18px;background-color:{theme.notice_bg};border-radius:6px;">
                  <p class="notice-text" style="margin:0;font-size:13px;color:{theme.notice_text};line-height:1.6;font-family:{theme.font_body};">
                    {message_html}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""


def _panel(title: str, rows: list[EmailPanelRow], *, theme: EmailTheme) -> str:
	row_html: list[str] = []
	for index, row in enumerate(rows):
		is_last = index == len(rows) - 1
		border = "" if is_last else f"border-bottom:1px solid {theme.panel_divider};"
		value_style = (
			f"font-family:'Courier New',Courier,monospace;font-size:17px;color:{theme.text};"
			"font-weight:700;letter-spacing:2px;"
			if row.is_code
			else f"font-size:15px;color:{theme.text};font-weight:600;font-family:{theme.font_body};"
		)
		row_html.append(
			f"""\
                  <tr class="panel-row">
                    <td style="padding:12px 0;{border}">
                      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                        <tr>
                          <td class="email-label field-label" style="font-size:12px;color:{theme.label};text-transform:uppercase;letter-spacing:1px;width:145px;font-family:{theme.font_body};font-weight:700;vertical-align:top;">
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
              class="panel-card" style="background-color:{theme.panel_bg};border-radius:8px;border-left:4px solid {theme.panel_border};">
              <tr>
                <td style="padding:22px 26px;">
                  <p class="email-label" style="margin:0 0 4px 0;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:{theme.label};font-family:{theme.font_body};font-weight:700;">
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


def _steps(title: str, steps: list[str], *, theme: EmailTheme) -> str:
	items = []
	for index, step in enumerate(steps, start=1):
		bottom_padding = "4px" if index == len(steps) else "10px"
		items.append(
			f"""\
              <tr>
                <td class="step-num" style="width:24px;vertical-align:top;padding:4px 0;font-size:14px;font-weight:700;color:{theme.step_num};font-family:{theme.font_body};">{index}.</td>
                <td class="step-text" style="vertical-align:top;padding:4px 0 {bottom_padding} 10px;font-size:14px;color:{theme.step_text};line-height:1.6;font-family:{theme.font_body};">{step}</td>
              </tr>"""
		)
	return f"""\
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label(title, theme=theme)}
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
{"".join(items)}
            </table>
          </td>
        </tr>"""


def _app_links(*, role: str, theme: EmailTheme, campaign: str) -> str:
	if role == "manager":
		return ""

	buttons: list[EmailCta] = []
	if theme.ios_app_url:
		buttons.append(EmailCta("iOS App Store", theme.ios_app_url, "ios_app", "Open the iOS App Store listing"))
	if theme.android_app_url:
		buttons.append(EmailCta("Android App", theme.android_app_url, "android_app", "Open the Android app listing"))

	if not buttons:
		return ""

	# A single available store link spans the full row; two split it evenly.
	if len(buttons) == 1:
		return f"""\
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-top:12px;">
              <tr>
                <td class="app-cell" style="width:100%;">
{_outline_button(buttons[0], theme=theme, campaign=campaign)}
                </td>
              </tr>
            </table>"""

	return f"""\
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-top:12px;">
              <tr>
                <td class="app-cell" style="width:49%;padding-right:6px;">
{_outline_button(buttons[0], theme=theme, campaign=campaign)}
                </td>
                <td class="app-cell" style="width:49%;padding-left:6px;">
{_outline_button(buttons[1], theme=theme, campaign=campaign)}
                </td>
              </tr>
            </table>"""


def _footer(*, theme: EmailTheme, expectation_note: str) -> str:
	return f"""\
        <tr>
          <td class="email-footer" style="padding:28px 40px 36px 40px;">
            <hr class="email-divider" style="border:none;border-top:1px solid {theme.footer_divider};margin:0 0 24px 0;" />
            <p class="email-muted" style="margin:0 0 16px 0;font-size:13px;color:{theme.footer_muted};line-height:1.7;text-align:left;font-family:{theme.font_body};">
              {expectation_note}
            </p>
            <p class="email-muted" style="margin:0;font-size:12px;color:{theme.footer_brand};text-align:center;line-height:1.8;font-family:{theme.font_body};">
              This is an automated message from <strong>{_h(theme.brand_name)}</strong>.<br />
              Please do not reply directly to this email.
            </p>
          </td>
        </tr>"""


def _render_email(
	*,
	theme: EmailTheme,
	title: str,
	preheader: str,
	eyebrow: str,
	heading: str,
	body_rows: str,
	footer_note: str,
) -> str:
	return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
{_head(title=title, theme=theme)}
<body class="email-bg" style="margin:0;padding:0;background-color:{theme.bg_outer};font-family:{theme.font_body};">
{_preheader(preheader)}
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="email-bg email-outer" style="background-color:{theme.bg_outer};padding:40px 0;">
    <tr>
      <td align="center" style="padding:0;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" class="email-card" style="max-width:600px;width:100%;background-color:{theme.card};border-radius:12px;overflow:hidden;box-shadow:{theme.shadow};">
{_header(eyebrow=eyebrow, heading=heading, theme=theme)}
{body_rows}
{_footer(theme=theme, expectation_note=footer_note)}
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
	"""Render the auditor temporary-credentials email (Playspace)."""
	theme = PLAYSPACE_THEME
	campaign = "auditor_credentials"
	web_cta = EmailCta(
		"Open Web Dashboard",
		theme.web_app_url,
		"web_dashboard_cta",
		"Open the web dashboard and sign in",
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(full_name)}</strong>,", theme=theme, margin="0 0 20px 0")}
{
		_paragraph(
			f"A {_h(product)} auditor account has been created for you by your workspace manager. Your temporary login credentials are below. Treat this information as confidential and update your password after your first sign-in.",
			theme=theme,
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
			theme=theme,
		)
	}
{
		_notice(
			"&#9888;&#65039; <strong>Action required:</strong> Sign in and change your temporary password immediately. Temporary credentials are short-lived and should never be shared.",
			theme=theme,
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
			theme=theme,
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Sign In & Reset Your Password", theme=theme, margin="0 0 16px 0")}
{_primary_button(web_cta, theme=theme, campaign=campaign, full_width=True)}
{_app_links(role="auditor", theme=theme, campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title=f"Your {product} Auditor Account",
		preheader=f"Your {product} auditor credentials are ready. Sign in and update your password immediately.",
		eyebrow=platform,
		heading="Your Auditor Account Is Ready",
		body_rows=body_rows,
		footer_note="If you were not expecting this account or believe it was created in error, contact your workspace administrator immediately and disregard this message.",
	)


def invite_html(
	invite_url: str,
	role: str,
	*,
	organization_name: str | None = None,
	invited_by_name: str | None = None,
	product: str = "yee",
) -> str:
	"""Render the workspace invitation email for managers and auditors.

	For manager invites, pass ``organization_name`` and ``invited_by_name`` so
	the recipient knows which workspace they are joining and who invited them.
	``product`` selects the design system (YEE vs Playspace).
	"""
	theme = theme_for_product(product)
	role_label = "manager" if role == "manager" else "auditor"
	is_manager = role_label == "manager"
	campaign = "manager_invite" if is_manager else "auditor_invite"

	invite_cta = EmailCta(
		"Accept Invitation",
		invite_url,
		"accept_invitation_cta",
		f"Accept your {theme.brand_name} invitation",
	)

	# Workspace panel: shown for managers (always) and auditors when org info
	# is available, so recipients always know which workspace they are joining.
	panel_rows: list[EmailPanelRow] = []
	if organization_name:
		panel_rows.append(EmailPanelRow("Organisation", organization_name))
	if invited_by_name:
		panel_rows.append(EmailPanelRow("Invited by", invited_by_name))
	workspace_panel = _panel("Workspace Details", panel_rows, theme=theme) if panel_rows else ""

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
{_paragraph(f"You have been invited to join an {_h(theme.brand_name)} workspace as a <strong>{_h(role_label)}</strong>.", theme=theme, margin="0 0 16px 0")}
{_paragraph(f"Use the button below to {_h(role_action)}. Once you accept, you will be guided through a short setup flow.", theme=theme, margin="0 0 28px 0")}
          </td>
        </tr>
{workspace_panel}
{_notice(notice_msg, theme=theme)}
{_steps(steps_title, steps_list, theme=theme)}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label(cta_label, theme=theme, margin="0 0 16px 0")}
{_primary_button(invite_cta, theme=theme, campaign=campaign, full_width=True)}
{_fallback_link(invite_url, theme=theme, campaign=campaign, content="plain_link")}
{_app_links(role=role, theme=theme, campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title=f"You have been invited to {theme.brand_name}",
		preheader=f"You have been invited to join an {theme.brand_name} workspace as a {role_label}. Accept your invite to get started.",
		eyebrow=theme.brand_name,
		heading="You Have Been Invited",
		body_rows=body_rows,
		footer_note="If you were not expecting this invitation, you can safely ignore this email. No account will be created without your action.",
	)


def submit_failure_html(
	auditor_name: str,
	place_name: str,
	audit_code: str,
	project_name: str,
) -> str:
	"""Render the background audit submit-failure notification email (Playspace)."""
	theme = PLAYSPACE_THEME
	campaign = "audit_submit_failure"

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(auditor_name)}</strong>,", theme=theme, margin="0 0 20px 0")}
{
		_paragraph(
			"We were unable to automatically submit your audit after it was queued while your device was offline. "
			"The audit data is safely saved on your device. Please open the Playspace app and resubmit manually.",
			theme=theme,
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
			theme=theme,
		)
	}
{
		_notice(
			"&#9888;&#65039; <strong>Action required:</strong> Open the Playspace app and submit your audit manually. "
			"Your recorded responses are still on your device and have not been lost.",
			theme=theme,
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
			theme=theme,
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Open the App", theme=theme, margin="0 0 16px 0")}
{_app_links(role="auditor", theme=theme, campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title="Audit Submission Failed",
		preheader=f"Your audit for {place_name} could not be submitted automatically. Open the app to resubmit.",
		eyebrow="Playspace",
		heading="Your Audit Could Not Be Submitted",
		body_rows=body_rows,
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
	"""Render the raw-data export completion email (Playspace).

	The export ZIP is generated in the requester's browser and downloads there
	directly, so this email confirms completion rather than carrying a download
	link. The call-to-action returns the user to the raw-data dashboard.
	"""
	theme = PLAYSPACE_THEME
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
			"included - see <strong>manifest.json</strong> inside the ZIP for the full list and reasons.",
			theme=theme,
		)
		if had_failures
		else ""
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"Hello <strong>{_h(requester_name)}</strong>,", theme=theme, margin="0 0 20px 0")}
{
		_paragraph(
			"Your raw-data export has finished building and the ZIP has downloaded in your browser. "
			"Each audit and combined report is included as a PDF alongside the data file you chose.",
			theme=theme,
			margin="0 0 28px 0",
		)
	}
          </td>
        </tr>
{_panel("Export Summary", panel_rows, theme=theme)}
{failure_notice}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Back to the dashboard", theme=theme, margin="0 0 16px 0")}
{
		_primary_button(
			EmailCta(
				label="Open raw data",
				url=dashboard_url,
				content_tag="export_ready_cta",
				aria_label="Open the raw data dashboard",
			),
			theme=theme,
			campaign=campaign,
		)
	}
{
		_fallback_link(
			dashboard_url,
			theme=theme,
			campaign=campaign,
			content="export_ready_fallback",
		)
	}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title="Your Export Is Ready",
		preheader=f"Your {entity_label} export finished and downloaded in your browser.",
		eyebrow="Playspace",
		heading="Your Export Is Ready",
		body_rows=body_rows,
		footer_note="You received this message because you requested a raw-data export from the Playspace dashboard.",
	)


def verification_html(verify_url: str) -> str:
	"""Render the account email-verification email (YEE)."""
	theme = YEE_THEME
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
{
		_paragraph(
			f"Welcome to {_h(theme.brand_name)}! You are one step away from activating your account.",
			theme=theme,
			margin="0 0 16px 0",
		)
	}
{
		_paragraph(
			"Confirm your email address using the button below. This ensures your account is secure and that you receive important notifications.",
			theme=theme,
			margin="0 0 0 0",
		)
	}
          </td>
        </tr>
{
		_notice(
			"&#9888;&#65039; <strong>This link expires in 24 hours.</strong> If it expires before you use it, sign in to request a new verification link.",
			theme=theme,
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
			theme=theme,
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Verify Your Email Address", theme=theme, margin="0 0 16px 0")}
{_primary_button(verify_cta, theme=theme, campaign=campaign, full_width=True)}
{_fallback_link(verify_url, theme=theme, campaign=campaign, content="plain_link")}
{_app_links(role="auditor", theme=theme, campaign=campaign)}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title=f"Verify Your {theme.brand_name} Account",
		preheader=f"One more step: verify your email to activate your {theme.brand_name} account.",
		eyebrow=theme.brand_name,
		heading="Verify Your Email Address",
		body_rows=body_rows,
		footer_note=f"If you did not create an {theme.brand_name} account, you can safely ignore this email.",
	)


def password_reset_html(reset_url: str) -> str:
	"""Render the password-reset email (YEE)."""
	theme = YEE_THEME
	campaign = "password_reset"
	reset_cta = EmailCta(
		"Reset My Password",
		reset_url,
		"reset_cta",
		"Reset your password",
	)

	body_rows = f"""\
        <tr>
          <td class="email-content" style="padding:36px 40px 0 40px;">
{_paragraph(f"We received a request to reset your {_h(theme.brand_name)} password.", theme=theme, margin="0 0 16px 0")}
{
		_paragraph(
			"Use the secure link below to choose a new password. If you did not request a password reset, you can ignore this email and your current password will keep working.",
			theme=theme,
			margin="0 0 0 0",
		)
	}
          </td>
        </tr>
{
		_notice(
			"&#9888;&#65039; <strong>This link expires in 2 hours.</strong> For security, any older reset link also stops working after your password changes.",
			theme=theme,
		)
	}
{
		_steps(
			"Reset Steps",
			[
				"Click <strong>Reset My Password</strong> below.",
				"Choose a new password for your account.",
				"Return to the login page and sign in with the new password.",
			],
			theme=theme,
		)
	}
        <tr>
          <td class="email-content" style="padding:28px 40px 0 40px;">
{_section_label("Choose a New Password", theme=theme, margin="0 0 16px 0")}
{_primary_button(reset_cta, theme=theme, campaign=campaign, full_width=True)}
{_fallback_link(reset_url, theme=theme, campaign=campaign, content="plain_link")}
          </td>
        </tr>"""

	return _render_email(
		theme=theme,
		title=f"Reset Your {theme.brand_name} Password",
		preheader=f"Use this secure link to reset your {theme.brand_name} password.",
		eyebrow=theme.brand_name,
		heading="Reset Your Password",
		body_rows=body_rows,
		footer_note=f"If you did not request a password reset for {theme.brand_name}, you can safely ignore this email.",
	)
