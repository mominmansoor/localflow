# LocalFlow

Free, unlimited voice dictation for Windows that runs entirely on your own PC.

I was paying for Wispr Flow and realised everything it does can run locally on a decent GPU. So I built my own. Hold a hotkey, talk, let go, and your words show up wherever your cursor is, cleaned up and punctuated. No subscription, no word limits, and nothing you say ever leaves your computer.

## What it does

**Dictation**
* Hold `Ctrl+Win` to talk, release to paste. Works in any app
* Double tap `Ctrl+Win` to go hands free, tap again to stop
* A small pill at the bottom of your screen shows you're being heard, with an optional live preview of your words
* Continuous mode for long notes: it pastes every time you pause and keeps listening
* Whisper mode boosts quiet speech so you can dictate softly in a shared room
* Speak any language and get English back with translate mode

**Cleanup**
* Removes ums, uhs and stutters instantly
* Handles self corrections like "meet at 5, no wait, 6" so you get "meet at 6"
* Say "comma", "new paragraph", "open quote" and it does the punctuation
* "First milk, second eggs" turns into a proper numbered list
* "3 p.m." becomes 3 PM, "john at example dot com" becomes john@example.com
* Pick how much cleanup you want (none, light, medium, high) and a writing style (neutral, formal, casual, very casual, verbatim)

**Your stuff**
* Snippets: say "my signature" anywhere and it types your full signature. Works with `${date}`, `${time}` and `${clipboard}`
* Secret snippets for things like passwords. They're encrypted with your Windows login and never shown in history
* Voice commands: say a phrase on its own to press keys, type text or open a program
* Teach it names and jargon it keeps getting wrong. Fix a word once in History and it's fixed forever

**Smart about where you're typing**
* Profiles for different kinds of writing (Chat, Email, Code, Notes, or make your own)
* App rules: formal in Outlook, casual in Slack and Discord, switched off in password managers
* AI rephrase: select some text, hold `Ctrl+Shift+Win`, say "make this more professional", let go

**Plus**
* A proper settings app where you can change everything without touching a config file
* Searchable history of everything you've dictated
* A local OpenAI compatible transcription API so tools like Cursor can use your GPU instead of a paid API

## Hotkeys

| Keys | What it does |
|---|---|
| Hold `Ctrl+Win` | Dictate |
| Double tap `Ctrl+Win` | Hands free mode, tap again to stop |
| Hold `Ctrl+Shift+Win` | Rephrase selected text with your voice |
| `Alt+Shift+Z` | Paste your last dictation again |
| `Ctrl+Alt+Win` | Pause all hotkeys |
| `Esc` | Cancel the current recording |

You can change any of these in Settings.

## What you need

* Windows 10 or 11
* An NVIDIA graphics card. 8 GB of VRAM is comfortable, I use a 16 GB card. RTX 50 series works fine
* About 6 GB of free disk space for the speech model, the AI model and the libraries
* [Ollama](https://ollama.com) if you want the AI cleanup (optional, dictation works without it)

No NVIDIA card? It still runs on your CPU, just slower.

## Installing

1. **Download this repo.** Click the green **Code** button above, then **Download ZIP**, and unzip it somewhere you'll keep it (like `Documents\LocalFlow`). Or clone it if you use git.

2. **Install Ollama** (for the AI cleanup). Grab it from [ollama.com](https://ollama.com) or run this in a terminal:
   ```
   winget install Ollama.Ollama
   ```

3. **Double click `install.bat`.** It will:
   * find Python, or offer to install Python 3.12 for you if you don't have it
   * set up everything in its own folder so it won't mess with anything else on your PC
   * download the speech model and the AI model

   The first install downloads a couple of GB, so give it a few minutes. If something goes wrong the window stays open with the error, and a full `install.log` gets saved in the folder.

4. **Double click `run.bat`.** A small icon appears in your system tray and the pill shows up at the bottom of your screen. That's it, hold `Ctrl+Win` and start talking.

Want it to start with Windows? Run `install.bat startup` instead, or flip the switch in Settings.

> If you also use Wispr Flow, quit it first. They both use `Ctrl+Win` by default.

## Using it

* **Double click the tray icon** to open Settings
* **Right click the tray icon** to switch profiles, cleanup level, microphone and more
* Running `run.bat` again while it's already open just opens Settings

The first dictation after starting takes a few seconds while the models load into your GPU. After that it's quick.

## If something's not working

Open a terminal in the LocalFlow folder and run:

```
.venv\Scripts\python -m localflow --doctor
```

It checks your GPU, microphone, models, Ollama and hotkeys, and tells you what's wrong.

A few common ones:

* **Install says Python wasn't found and winget fails.** The installer will download Python straight from python.org instead. If that's blocked too, install Python 3.12 from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH".
* **"GPU failed, using CPU Whisper".** Update your NVIDIA driver and run `install.bat` again.
* **"Model not pulled".** Make sure Ollama is running and run `ollama pull qwen3:4b-instruct`.
* **Text doesn't appear in one specific app.** Some apps running as admin block input from normal apps. Either run LocalFlow as admin or set delivery to "Type it out" in Settings.
* **Want to see what's happening?** Use `debug.bat` instead of `run.bat`. It shows timings for every dictation.

## Privacy

Everything runs on your machine. Speech recognition is [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your GPU and the AI cleanup is a small model running in Ollama on your PC. The only time it touches the internet is downloading the models during install. Your history is stored in `data/history.db` and you can turn it off or set it to auto delete in Settings.

## How it works

```
hotkey → mic → Whisper on your GPU → fix names, snippets, punctuation, ums
       → local AI cleanup in your chosen style → pasted where your cursor is
```

Every AI output goes through a check before it's pasted. If the model tries to answer your question instead of cleaning it up, or takes too long, LocalFlow just uses the rule based cleanup instead, so dictation never breaks.

Built with Python, PySide6, faster-whisper and Ollama.

## For developers

```
pip install pytest numpy PySide6-Essentials
python -m pytest -q
```

Command line options are listed with `python -m localflow --help`.

## License

MIT, do whatever you want with it.
