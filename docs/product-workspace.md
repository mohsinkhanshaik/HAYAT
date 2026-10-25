# Product workspace

Open **Business → Products** after restarting HAYAT.

1. **New product** creates a local draft. Enter its audience, brief, draft price and explicit license terms. **Edit listing** updates the same draft and checks for concurrent changes.
2. Choose **Weekly planner**, **Content calendar**, or **Research report**, then **Create starter files**. HAYAT generates a branded Word/PDF guide, README, a workbook with an overview and instructions, and a four-slide review presentation. The workbook has formulas, a bounded working area and status dropdowns. These are starter templates, with clearly labelled examples and content to review.
3. Use **Add files** to include existing deliverables, or **Remove selected** to change the selection. Choices persist across restarts. **Preview / open** opens PDF in the paged viewer or other formats in their default application.
4. **Edit file** opens the text editor, a native Word/PowerPoint exact-replacement editor, or an Excel cell editor. Word main-body paragraphs/tables and PowerPoint text boxes are supported. Excel previews default to A1:F30 and can cover up to 2,000 cells; formulas are shown as formulas. Choose Text, Number, Formula, Boolean or Empty when editing a cell. Text is stored literally, including a leading equals sign. Non-finite numbers and oversized cell strings are refused. Saves check the file SHA256 and keep recoverable backups.
5. **Refine with HAYAT** prepares an editable chat request for your configured model and existing file tools. Enter your requested improvements before sending it. Native starter generation and direct editing work without a model.
6. **Build delivery ZIP** packages the selected files, listing, license and checksums. **Verify delivery** rechecks the archive and reports altered ZIP contents, changes to source files, or a listing change that requires rebuilding. Earlier ZIPs retain their original snapshots. File integrity is separate from content accuracy, licensing review and demand validation.

Generation, catalog reads, packaging and verification run off the interface thread. Product controls remain visible at the tested 980×820 and 760×650 window sizes, and closing the workspace cancels pending UI delivery. The existing Revenue workspace continues to provide research, AI content/prototype generation and recorded business evidence. Its record-details popup now opens correctly with themed, readable text.

The added tools are `product_details`, `product_update`, `product_set_files`, `product_build_starter`, `product_verify_bundle`, and `read_spreadsheet_for_edit`. Product listing changes cannot change currency after sales have been recorded, so historical revenue is kept in its original currency.

Checkout remains deferred. Product drafts and ZIPs are local; no payment is collected or income invented. Use the existing product sales ledger for actual transaction references and the business wallet for recorded receipts and withdrawals. HAYAT does not hold funds or issue redeemable credits.

Native Word, PowerPoint and Excel edits remain limited to 50 MiB. UTF-8 text editing remains limited to 5 MiB. Arbitrary binary files, PDF text replacement, macros, Word headers/footers and unsupported embedded objects require their native editors or regeneration from source.

Validation on 2026-10-04: the full automated suite passed 3,099 tests, with nine environment-dependent or opt-in checks skipped. The separate real local-model revenue check passed, producing a fictional proposal and a checked prompt-pack ZIP without publication or outreach. Dependency, lint and whitespace checks passed. The generated Hamuzair creator kit was independently reopened as Word, Excel and PowerPoint, its ZIP/source hashes checked, and its PDF guide rendered and visually inspected. Windows desktop visual verification was unavailable because the computer-use native connection was missing; GUI behavior was checked through Tk integration tests.

Model-free runtimes now also keep conversation and advisor selection disabled. Previously their adaptive conversation selector could still choose a live provider and stall offline checks.
