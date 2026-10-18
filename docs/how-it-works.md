# How the privacy agent works, in plain English

_Keep this file up to date whenever the behaviour changes. It is written for someone who has never seen the code._

## The idea in one paragraph

Nanobrowser is a Chrome extension that lets an AI model use your browser. Normally, everything on the page
(your name, your email, the screenshot) is sent to that model. We change two things. First, **the extension hides
personal data before anything leaves the browser**: real values become placeholders such as `[[EMAIL_1]]`, and
screenshots get the same values painted over. Second, **the model is our own server**, running on our own laptops: a
text model (qwen3:14b) that decides what to do, helped by a vision model (Gemma 4) on a second, Windows laptop that looks
at the masked screenshot. The real values come back only at the last moment, when the extension types them into the
page.

## The machines

| Where | What runs there | Talks to |
|---|---|---|
| Laptop A | Chrome with our Nanobrowser fork | the orchestrator, over `http://…:8000/v1` |
| Laptop A | **orchestrator** (FastAPI) + **qwen3:14b** in Ollama | the VLM worker over **wss** |
| Laptop B (Windows) | **VLM worker** + **Gemma 4** (vision) in Ollama | nothing else |

Nanobrowser is set up with an "OpenAI-compatible" provider whose address is the orchestrator. Its model list says the
model takes images, so Nanobrowser offers the `screenshot` tool that the vision step needs. Nanobrowser doesn't
know that the "model" is a pipeline.

## One step, from the page to the click

1. **Nanobrowser looks at the page.** It asks the browser layer for a `snapshot`, a text outline of the page such as
   `[e3] textbox «Email» = «jane@x.org»`. With vision on, it can also take a `screenshot`.
2. **The screenshot is masked when it is taken.** The layer already paints over passwords and card numbers. We added a
   hook so it also paints over every element whose text contains a personal value: anything the vault knows, or
   anything the detectors find. Only after that is the image recorded as "allowed to leave".
3. **The request is cleaned on its way out.** Every request to the model goes through our `fetch` wrapper:
   - Values already in the vault are replaced by their placeholder first, including their URL-encoded and
     form-encoded forms (`jane.doe%40example.com`, `Jane+Doe`), because pages often echo submitted data like that. This catches a value we typed earlier
     when it shows up again on the page.
   - Then the detectors run (also on a decoded copy of the text): a name you state ("my name is …"), email, phone, card, ID numbers, the value of any field whose label says "name" (in the outline's real `textbox "Full name" = "…"` form),
     "address", "date of birth" and so on, and your own "hide this" rules. Each new value gets a new placeholder.
   - Any image that was not masked by the layer is replaced by the text "screenshot withheld".
   - Redaction repeats until no new placeholder appears, so a value found late is also replaced earlier in the request.
   - A final **leak check** searches the cleaned request again. If any real value or detector hit remains, the
     request is **not sent**, and the run stops with a notice.
4. **The orchestrator receives only placeholders and masked images**, and works out what kind of request it is:
   - the connection test: answered at once;
   - Nanobrowser's final "wrap up" call: qwen3:14b writes the summary;
   - a request with no tools: plain text, or the vision model for the image test;
   - a normal step of the task: the step pipeline below.
5. **The step pipeline (the server-side feedback loop):**
   - If the last action was refused because of an unknown placeholder, ask the user. No model call.
   - If there is no fresh page outline (the user was just asked something, or the last action opened another page: its
     result says `navigation: new-document`, `new document` or `URL →`), ask Nanobrowser for a `snapshot`. No model
     call. Without this, the agent kept acting on the old page's refs after "Blank form" opened the Google Forms editor.
   - **Let the page finish loading first.** Before the server looks at the page after an action (a snapshot or a
     screenshot), it asks the layer to `wait` until the page has **settled**: the document has loaded and nothing in it
     has changed for half a second. The limit is 15 seconds (`SETTLE_TIMEOUT_S`). The layer's own wait after a click or
     `navigate` stops at 5 seconds, and Google Sheets and Forms were still building their page after that. A page that
     never settles is still looked at, and the next action's reason tells you "the page was still loading".
   - With vision on, ask for one `screenshot` per new outline, with the ref labels (`a1`, `a5`, …) drawn on top of
     the masked image. The vision model on Laptop B (over **wss**) **describes every new page**: the page type, any
     popup, visible errors, the fields it can see. (It used to run only on outlines with fewer than 8 elements, so it
     never looked at a Google page. `PERCEIVE=thin` brings the old rule back, `PERCEIVE=never` turns it off.)
   - **After each action, the outline decides whether a picture is needed.** The layer's result already says what
     happened, so the server reads it:
     - **Sure, no picture:** a value the layer read back and found right (`expect: met`), an action that opened a new
       page (it gets a new outline and description anyway), and anything that is not an action on an element.
     - **Not sure, so a fresh picture:** a click, key press or hover with no confirmed effect, a value that could not be
       read back, typing into whatever has focus, and any action on a page whose outline shows little of it (fewer
       than 8 elements, as in a canvas app). The vision model is asked about **that action**: "did it take effect,
       what is visible now because of it, and where?" Its answer goes into qwen3:14b's prompt as "what the screen
       shows after your last action".
     A sure result says the action happened, not that it hit the right thing (typing into the Name Box reads back
     fine), so the Name Box rule and the final check below still apply.
   - If Laptop B doesn't answer within 60 seconds (`VLM_TIMEOUT_S`), continue without it, but **never silently**: the
     reason goes into the log, and the next action's reason, which Nanobrowser shows you, says "Text only: the vision
     model did not …" and why.
   - The vision model only runs on a screenshot taken just now. Measured on Laptop B: a description takes 4–37
     seconds, a check about 9 seconds.
   - Build a **short** prompt for qwen3:14b: the **current request** (the last thing you asked in the chat; earlier
     requests are listed as already handled), the active tab, and a list of the fields it has already filled: the task, the screen description, the outline, the last few results,
     and any reasons earlier answers were rejected. Nanobrowser's 19 tool definitions (about 9k tokens) are not
     included.
   - qwen3:14b answers with one small JSON command (navigate, click, type, scroll, ask_user, done), for example `{"action":"type","ref":"e3","value":"[[EMAIL_1]]"}`.
     Ollama forces the answer into that shape, so we don't rely on the model's own tool-calling.
   - **Check** the command. It must not repeat itself, for example by navigating to the page that is already open. the ref must exist on the page, and every placeholder must be one the browser actually
     sent. A changed placeholder such as `[EMAIL_1]` is rejected.
   - A risky click (Submit, Pay, Delete, Send…) is refused while any placeholder from the task has not been typed.
   - Before a risky click, take a fresh screenshot and ask the vision model to **verify** the click. For a click by
     position, its "no" blocks the click. For a click on a ref, its "no" is only recorded, because the 7B vision model
     wrongly said "not visible" in every test. The ref check and Nanobrowser's own confirmation still apply.
   - On the screenshots, a field covered by a grey box has a value; an empty white field is empty.
   - **A pop-up comes first.** If the outline shows a dialog (`dialog "Alert" (modal)`), qwen3:14b's prompt says so and
     lists the dialog's buttons ("answer it first; its title is not a button"). While a modal or alert dialog is open,
     a click or typing on anything behind it is refused, with the dialog's buttons named. If an action fails because
     something covers the target (`OBSCURED`, for pop-ups built without dialog markup), the prompt says the last action
     was blocked and by what, and a fresh picture goes to the vision model, which is asked to name the pop-up and its
     buttons. (On IRCTC the model clicked the language dialog's title and typed into the form behind it.)
   - **Typing into a spreadsheet's Name Box is refused.** Google Sheets draws its cells on a canvas, so the outline has
     no cell A1, only the Name Box (the box at the top left that shows "A1"). A run typed "hello" there. Now the model
     is told that the Name Box holds a cell address, and to type without a ref, which types into the selected cell.
   - Any problem goes back to qwen3:14b as a note, and it tries again, at most twice. After that, ask the user.
   - **Clicking through the grid, for what has no ref.** The outline gives most elements a ref, and a ref click is exact
     and instant, so it stays the normal way. Some things have no ref: a spreadsheet cell (Google Sheets draws its
     grid on a canvas), an icon, part of a picture, a pop-up built without markup. For those, qwen3:14b answers with
     `"visual"`: a description such as "cell A1" or "the English button in the language pop-up". Then:
     1. The server takes a screenshot **without** the red labels (masked as always) and draws a blue chessboard grid
        over it (columns A–P, rows 1–9). The vision model says what it sees and which cell holds the thing ("D7").
     2. That cell and its neighbours are cut out, enlarged, and divided into 36 numbered squares. The vision model
        names the square.
     3. The centre of that square becomes a point. The vision model checks it on the screenshot with a pink marker
        ("is the marker on cell A1?"); its no stops the click and goes back to qwen3:14b.
     4. The browser layer turns the point into the element under it (the canvas, or the button around an icon) and
        runs **the same checks and confirmations as for a ref**: a "Submit order" button found on the grid still asks
        you first. The point is pressed exactly where it was found, which is what a canvas needs. A point is only valid
        on the page as the screenshot showed it: if the page scrolled, resized or loaded since, the layer refuses it
        (`STALE_POINT`). The receipt names the element and the point (`[a17] canvas "Grid" at (212, 340)`).
     This costs three vision calls, about 30–60 seconds, so it is a fallback, not the normal path. Points are allowed
     only for vision models (the `vision` capability), and the model never gives coordinates itself.
   - **Clicks that keep failing on one element** (for example a button something covers) go up a ladder with an end.
     After one failure, a fresh labelled screenshot goes to the vision model, which names the label on the visible
     element, and the click goes there. After two, **the grid** (above) finds the element and the click goes to that
     point. After three, the keyboard: focus the element and press Enter. After four, or six failed actions on one
     page, the step is handed to you ("please click it, then press Done") and the retries stop. When you press Done,
     the count starts again: before, it kept asking you to take over after you had fixed the page. Without the vision
     model, the ladder skips its vision and grid steps.
   - **"Done: success" is refused** while a value from your request (such as [[PHONE_1]]) has not been typed.
   - **"Done: success" needs a look at the page after the last action.** If anything was clicked, typed or opened
     after the latest outline, the server takes a new `snapshot` (and then a screenshot) instead, and the model decides
     again from what the page shows now.
   - **With vision on, the vision model checks the claim** on a final screenshot taken **without the red labels**
     (they cover the page's own content). It must first name its **evidence**: where on the page it sees each part
     of the request (for a spreadsheet, the cell), and only then say yes or no. It is told the Name Box is not a cell.
     (Before, it answered yes first and explained after, and said "cell A1 clearly shows hello" when A1 was empty and
     hello was in the Name Box.) The evidence is added to the summary you see ("Checked on the final screenshot:
     …"). Its no goes back to qwen3:14b as a note; if the model still says done after the retries, you are asked to
     look. If the vision model cannot answer (link down, no answer in time, an answer that isn't JSON), the outcome
     becomes **partial**, and the summary says the final page was not checked and why.
   - The command is then converted into the tool call Nanobrowser expects (for example `set`, with the target ref
     and the value).
6. **Back in the browser, the real value goes in.** Just before the layer runs the tool, the extension swaps each
   placeholder for its real value. If a placeholder is unknown or slightly changed, the call is refused and nothing
   is typed. The conversation history keeps the placeholders, so the real values never go back to the model.
7. **Nanobrowser's own safety rules still apply.** Submit, send and delete ask you first (in Ask mode), and payments
   and passwords are always handed to you.
8. **The result goes into the next request.** The receipt (what changed on the page) is part of the next request,
   so a failed action shows up in the next step's prompt.

## The user's side of the loop

- You confirm risky actions (Nanobrowser's own approval cards).
- When the agent asks you something (`ask_user`), your typed answer is put into the vault straight away. Next time it
  is sent as a placeholder. Short replies such as "yes" stay as they are.
- "Hide this" rules are kept in `chrome.storage.local` under `privacyRules`. They can be a literal value, a pattern, or
  a field label. The rules editor in the side panel is still to be built.

## What we promise, and what we don't

- **Only cleaned data leaves the browser.** Text is cleaned and leak-checked, and images must be ones the layer
  masked or files you attached yourself.
- **A name the detectors can't recognise is not hidden** until it is in the vault: you typed it into a field, it is in
  a field labelled "name", it is one of your rules, or it was an `ask_user` answer. Add your own name as a rule.
- **Text drawn inside pictures, canvas or video is not detected**, so it is not masked.
- The vault lives in the service worker's memory. After Chrome restarts the worker, earlier placeholders are unknown.
  The extension then refuses them and the agent asks you again.
- The vault is shared by all conversations in the browser. A placeholder from one chat would resolve in another.

## How to run it

See [`../server/README.md`](../server/README.md) for the servers. For the extension, open `client/` and run
`pnpm install` and `pnpm build`, load `client/dist` unpacked in Chrome, and add a provider of kind
"OpenAI-compatible" with the base URL `http://<laptop A>:8000/v1` and model `privacy-agent`.
