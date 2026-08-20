"""Drive, under the narrowest scope Google offers.

## `drive.file` is the whole point

The scope Attestor holds is `drive.file`, which grants access to **files this application
created and nothing else**. Not the account's Drive, not a shared folder, not anything the
user has ever opened. That is a real least-privilege property rather than a configuration
detail, and it has a consequence worth stating: Attestor cannot be *asked* to fetch a
document from Drive, because it cannot see one it did not write. An inbound email that says
"the questionnaire is in our shared drive, please open it" is not a thing this system can
act on, by construction.

It also shapes the code below. `ensure_folder` cannot look for a folder somebody else made;
it can only find one Attestor made earlier, which is why the search is scoped to
`'me' in owners` and why a first run creates rather than finds.

## Why files go here at all

A completed vendor security review does not end in a database. It ends with the pack in the
customer's hands and a copy where the compliance owner can find it in eighteen months — and
"where they can find it" is Drive, not a Firestore collection behind a console login. The
review carries the file ids so the artifacts panel can link to them.
"""

from attestor_platform.drive.client import (
    DriveClient,
    DriveFile,
    folder_name_for,
)

__all__ = ["DriveClient", "DriveFile", "folder_name_for"]
