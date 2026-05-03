# SendGrid Email — Secretary CRM Tool Package

Version: 1.0.0  
Author: DesignLeaf CRM  
Risk level: medium

## What this tool does

Enables transactional and marketing email sending via [SendGrid](https://sendgrid.com) directly from Secretary CRM. Supports sending to individual contacts, using SendGrid Dynamic Templates, and checking delivery status.

## Required slots

| Slot | Type | Required | Description |
|------|------|----------|-------------|
| `api_key` | Secret | Yes | SendGrid API key with **Mail Send** permission |
| `from_email` | Config | Yes | Verified sender email (must be registered in SendGrid) |
| `from_name` | Config | No | Display name shown as email sender |

## Setup instructions

1. Log in to [SendGrid](https://app.sendgrid.com) → Settings → API Keys
2. Create a new API key with **"Restricted Access"** and enable **"Mail Send"**
3. Copy the key — it is shown only once
4. After installing this package, go to **Tools → SendGrid Email → Configure**
5. Enter your API key (it will be encrypted and stored securely — never shown in plain text)
6. Enter your verified sender email address
7. Click **Test Connection** to verify

## Permissions granted

- `email.send` — allows CRM to send emails to contacts
- `email.read_logs` — allows viewing email delivery history

## Voice commands

- English: *"send email to [contact name]"*
- Czech: *"poslat email [jméno kontaktu]"*

## Uninstalling

Go to **Tools → SendGrid Email → Uninstall**.  
To also remove stored secrets, check **"Purge stored credentials"** before confirming.
