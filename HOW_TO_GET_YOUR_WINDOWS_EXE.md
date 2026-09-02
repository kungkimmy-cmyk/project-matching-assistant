# How to get your Windows TEST .exe (no Python, no command prompt for you)

I cannot build or launch a Windows `.exe` myself -- this environment
has no Windows machine and no network to install a Windows toolchain.
Same situation as before, same solution: this project builds itself
automatically on GitHub's free Windows machines.

## Step-by-step (all in your web browser)

1. **GitHub account** (free, if you don't have one): https://github.com/signup

2. **New repository**: click "+" top right -> "New repository". Name
   it anything (e.g. `project-matching-assistant`). Public or Private
   both work. Click "Create repository."

3. **Upload this whole project folder**: on the repository page, click
   "uploading an existing file". Drag the entire extracted project
   folder into the browser window. Click "Commit changes."

4. **Wait for the build**: click "Actions" at the top. "Build Windows
   Release (TEST BUILD)" will be running -- wait for the green
   checkmark, usually 2-4 minutes.

5. **Download**: click the finished run, scroll to "Artifacts," click
   `Project_Matching_Assistant_TEST_BUILD_Windows` to download.

6. **Unzip it.** Inside:
   ```
   Project_Matching_Assistant_TEST_BUILD_Windows/
       Project_Matching_Assistant_TEST_BUILD.exe
       matcher_config.json (created on first run)
       README.pdf
   ```

## Running it

1. Double-click `Project_Matching_Assistant_TEST_BUILD.exe`
2. Click **Add Factory Folder...** -- browse to your local factory
   quotation folder(s) on your computer, add as many as you have
3. Click **Add Customer Folder...** -- same, for customer quotation
   folder(s)
4. (Optional) Click **Select Sales Report (SOLD history)...** -- browse
   to your local Mimosa Sales Invoice Report file
5. Click **Analyse**, then save the results when prompted

## Where things land

- The `.exe` itself, plus `matcher_config.json`, `Matching_Log.txt`,
  `Matcher_Crash_Log.txt`, and a `photo_store` folder: all next to
  wherever you put the `.exe`
- `Matching_Results.xlsx`: wherever you choose to save it when prompted

## What to send back after your run

1. `Matching_Log.txt` and `Matcher_Crash_Log.txt` -- always, even if
   nothing went wrong
2. Anything that crashed, looked wrong, or took unexpectedly long
3. The saved `Matching_Results.xlsx` if you're comfortable sharing it
   back here (it contains real factory costs) -- otherwise a
   description of what you see is enough

## If you'd rather not touch GitHub

Ask anyone with Windows + a few minutes to run `build_exe.bat` from
the project folder (needs Python on THEIR machine, not yours). Same
result either way.
