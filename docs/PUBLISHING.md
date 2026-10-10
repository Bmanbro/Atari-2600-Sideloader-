# Publish the source on GitHub

This folder is a standalone source release. It contains the scripts, payload
source, firmware profile, license and public instructions. Publish its contents
as the repository root so `README.md` appears on the repository's front page.

Keep `local/`, game files, firmware packages, generated images, console backups
and installation receipts private. `.gitignore` covers these common paths and
extensions. A browser upload does not apply your local `.gitignore`, so use a
clean source copy when dragging files into GitHub.

## Git command line

Create an empty repository on GitHub using
[GitHub's repository creation instructions](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository).
Choose a name such as `atari-pacman-sideload`. When uploading this existing
folder, leave GitHub's README, license and gitignore initialization unchecked;
the files are already included here.

Open a terminal in this source folder:

```sh
git init -b main
git add README.md LICENSE CHANGELOG.md .gitignore .gitattributes \
  sideload.py device.py build_menu.py update_games.py prepare_7800.py \
  profile.json payload docs
git status --short
git diff --cached --stat
git commit -m "Publish Pac-Man 2600+ internal game menu"
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Replace the remote URL with your own. Inspect the staged file list before
committing. The explicit source list avoids accidentally adding nearby game
or firmware directories. Authenticate using your normal GitHub method.

## Browser upload

After creating a repository, use **Add file → Upload files** (or the upload
link on an empty repository) and upload the source files and the `payload/`
and `docs/` folders. Include the license and hidden `.gitignore` and
`.gitattributes` files if your file chooser hides them. Do not upload your
entire working Atari directory. Follow
[GitHub's upload instructions](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

This package has no remote repository configured and publishes nothing by
itself. The local source ZIP can be kept as a release artifact, but extract it
before uploading individual source files if you want GitHub to display them.
