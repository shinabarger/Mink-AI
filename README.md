<img src="mink/static/logo.svg" width="72" alt="Mink logo, a smiling mink face">

# Mink

Free, private transcription for long recordings, with speaker labels you can fix in a few clicks. Runs entirely on your Mac.

## Requirements

- A Mac with Apple Silicon (M1 or newer) running macOS 14 or later
- About 5 GB of free disk space
- An internet connection for the first run (to download the models)

## Install

1. **Get the code.** Either:
   - Click the green **Code** button on this page, choose **Download ZIP**, and unzip it into your Documents folder, or
   - Clone it with [GitHub Desktop](https://desktop.github.com): **File → Clone Repository**, paste this repository's URL, and choose your Documents folder.
2. **Open Terminal** (press `Cmd + Space`, type *Terminal*, press Enter).
3. **Start Mink.** Paste these two lines, using the folder name you ended up with:
   ```
   cd ~/Documents/mink
   bash start.command
   ```
   The first launch installs everything it needs, which takes a few minutes. Your browser then opens Mink at http://127.0.0.1:8765.
4. **Leave the Terminal window open** while you use Mink. Press `Ctrl + C` in it to quit.

To start Mink again later, repeat step 3.

## Add your own Hugging Face key (recommended, free)

Mink works out of the box, but its speaker labels are much better with the free [pyannote](https://huggingface.co/pyannote/speaker-diarization-community-1) model. That model needs your own free Hugging Face access token. No key is included in this project; each person uses their own.

**Get a key (about 5 minutes):**

1. Create a free account at [huggingface.co/join](https://huggingface.co/join).
2. Open the [speaker model page](https://huggingface.co/pyannote/speaker-diarization-community-1) while signed in, fill in the short form, and click **Agree**. Without this step the key won't work.
3. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens/new?tokenType=read), choose the **Read** token type, give it any name (for example `mink`), and click **Create token**.
4. Copy the token. It starts with `hf_`.

**Put the key in Mink:**

1. Start Mink and click **Settings** in the left sidebar.
2. Paste the token into the box under **Better speaker labels** and click **Save**.
3. The bottom of the sidebar should now say **pyannote community-1**.

Your key is saved only on your Mac, in `~/Mink/settings.json`, which is outside this project folder. Never paste it into any file in this project, and never share it publicly. If it leaks, delete it on the Hugging Face tokens page and create a new one.

## Updating

Download or pull the latest version into the same folder, then restart Mink (`Ctrl + C`, then step 3).

## Where your data lives

Recordings, transcripts, and settings are stored in `~/Mink`. Delete that folder to remove everything.

## Accessibility

Mink aims for WCAG 2.2 AA: full keyboard use, visible focus, screen reader labels, and text contrast of at least 4.5:1. Checked with [axe-core](https://github.com/dequelabs/axe-core). If something doesn't work for you, please open an issue.

## Development

```
uv run --group dev pytest
uv run --group dev ruff check .
```

## License

MIT. Speech recognition by [Whisper](https://github.com/openai/whisper); speaker labels by [pyannote](https://github.com/pyannote/pyannote-audio) and [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). Each model has its own license.
