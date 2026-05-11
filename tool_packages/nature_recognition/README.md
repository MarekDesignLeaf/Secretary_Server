# Nature Recognition

AI-powered plant identification, plant health diagnosis and mushroom recognition for Secretary CRM.

## What it does

After installation, three tiles appear in your **Tools Hub**:

- **Plant identification** — Take a photo to identify a plant species, care requirements and description
- **Plant health check** — Diagnose diseases, pests and nutritional deficiencies from a photo
- **Mushroom identification** — Identify mushroom species and edibility (⚠️ always verify with an expert before eating)

## Requirements

- **OpenAI API key** with access to `gpt-4o` (vision model)
- Android app version 1.0.0+

## Installation

1. In the app, open **Tools → Tool packages → Install**
2. Upload this `.zip` file
3. Enter your OpenAI API key when prompted
4. The three nature recognition tiles will appear in the Tools Hub immediately

## Configuration slots

| Slot | Required | Description |
|------|----------|-------------|
| `openai_api_key` | ✅ | Your OpenAI API key |
| `model` | No | Vision model (default: `gpt-4o`) |
| `max_image_tokens` | No | Max tokens per request (default: `800`) |

## Cost

This tool uses your own OpenAI API key. Typical cost per analysis: **$0.003–$0.010** with gpt-4o.

## Uninstalling

Uninstalling removes the three tiles from your Tools Hub. Your recognition history in the database is preserved.
