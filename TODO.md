# TODO

- [x] The prompt context now always takes the whole conversation, which makes no sense (introduce a proper "context" = tags and their enrichment + history up until that point)
- [x] New llamora replies appear at the top, then on refresh are at the bottom
- [x] Delete replies / user messages
- [x] Better UI for the different kinds of replies
- [x] Message kind in the reply? (mimic icon)
- [x] Stop button?
- [x] Disable replies after today?
- [x] Day opening is broken (and where to put it), midnight refresh seems broken
- [ ] Edit for user messages?
- [x] Confirm / modal for entry deletion?
- [x] No concept of time / timestamp on messages (should be accessible somewhere)
- [x] A lot of code clean up / refactoring
  * Two stage LLM call is now redundant, should be removed
  * Form submitting logic can be simplified
