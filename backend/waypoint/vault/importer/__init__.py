# SPDX-License-Identifier: Apache-2.0
"""Resume import: file checks, sandboxed text extraction, and text-to-draft parsing.

Uploaded files are untrusted. They are type-checked by content, size- and
zip-bomb-checked before parsing, and parsed in a separate short-lived process
with a timeout and no access to the app's secrets.
"""
