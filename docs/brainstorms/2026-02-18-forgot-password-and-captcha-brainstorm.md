# Forgot Password Email + CAPTCHA Protection

**Date:** 2026-02-18
**Status:** Brainstorm
**Feature area:** Authentication

---

## What We're Building

Two additions to the existing auth system:

1. **Password reset via email** - "Forgot password?" flow using Django's built-in password reset views, sending reset links via Brevo (Sendinblue) SMTP on the free tier (300 emails/day).

2. **Cloudflare Turnstile CAPTCHA** on signup and password reset forms to prevent bot abuse and protect the email quota. No email verification required.

## Why This Approach

- **No email verification** - CAPTCHA on signup prevents bot account creation. If a user enters a bogus email, they simply can't reset their password later. That's their problem, not ours. This avoids email costs for verification and keeps the signup flow frictionless.
- **Brevo SMTP** - 300 emails/day free tier is far more than enough for password resets on a personal/small project. Django's `EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'` works out of the box with Brevo's SMTP credentials.
- **Cloudflare Turnstile** - Free with no request limits. Invisible to most users (no puzzles). Privacy-friendly. Simple integration: a JS script + hidden form field + server-side verification API call.
- **Django's built-in password reset** - `PasswordResetView`, `PasswordResetDoneView`, `PasswordResetConfirmView`, `PasswordResetCompleteView` handle the entire flow including secure token generation and expiry. No need to reinvent this.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Email verification | Skip it | CAPTCHA prevents bots. Bogus emails only hurt the user (no reset). Saves email spend and signup friction. |
| Email provider | Brevo (Sendinblue) | 300/day free, SMTP works natively with Django, established provider. |
| CAPTCHA solution | Cloudflare Turnstile | Free, invisible, privacy-friendly, no user friction. |
| CAPTCHA placement | Signup + password reset | Protects account creation and email quota. Login left unprotected (lower risk). |
| Password reset implementation | Django built-in views | Secure token handling, battle-tested, minimal code. Just need custom templates. |
| Reset page styling | Neobrutalist (match existing design) | Consistent UX. Templates extend base.html with VT323, neo shadows, brand colours. |

## Scope

### In scope
- Brevo SMTP email backend configuration (settings.py + env vars)
- Django password reset flow (4 views, 4 templates)
- Cloudflare Turnstile on signup form
- Cloudflare Turnstile on password reset request form
- "Forgot password?" link on login page
- Neobrutalist-styled templates for all reset pages
- Password reset email template (HTML)

### Out of scope
- Email verification on signup
- CAPTCHA on login form
- Rate limiting (beyond CAPTCHA)
- Custom password reset tokens or expiry logic
- Marketing/notification emails

## Implementation Notes

### Brevo setup
- Create free Brevo account, get SMTP credentials
- Add to `.env`: `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `DEFAULT_FROM_EMAIL`
- Configure `EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'` in settings.py

### Turnstile setup
- Create Cloudflare Turnstile widget (free), get site key + secret key
- Add to `.env`: `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`
- Frontend: Add Turnstile JS script + `<div class="cf-turnstile">` to signup and reset forms
- Backend: Verify token via POST to `https://challenges.cloudflare.com/turnstile/v0/siteverify` in form validation or view logic

### Password reset templates needed
- `password_reset_form.html` - Enter email (with Turnstile)
- `password_reset_done.html` - "Check your email" confirmation
- `password_reset_confirm.html` - Enter new password (from email link)
- `password_reset_complete.html` - "Password updated" success
- `password_reset_email.html` - Email body template (HTML)
- `password_reset_subject.txt` - Email subject line

### URL patterns (Django convention)
- `password-reset/` - Request form
- `password-reset/done/` - Confirmation page
- `password-reset-confirm/<uidb64>/<token>/` - Set new password
- `password-reset-complete/` - Success page
