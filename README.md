# Telegram Content Automation

Python automation tool for processing and publishing Telegram video content.

## What it does

- Reads recent posts from a source Telegram channel
- Filters promotional and unwanted content
- Downloads video posts
- Rewrites captions through the OpenAI API
- Publishes approved videos to a target Telegram channel
- Prevents duplicate publishing
- Runs automatically on a configurable schedule
- Logs processing errors and activity

## Tech Stack

- Python
- Telethon
- OpenAI API
- AsyncIO
- JSON-based state tracking

## Configuration

Credentials and channel settings are loaded from environment variables and are not stored in the source code.

## Purpose

This project was built as a practical automation workflow for handling repetitive Telegram content processing and scheduled publishing.
