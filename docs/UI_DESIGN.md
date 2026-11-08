# Dashboard interface

The administrator workspace uses the existing Flask/Jinja templates and plain
CSS/JavaScript. No new frontend build or third-party assets are required.

## Visual system

| Element | Specification |
| --- | --- |
| Canvas | Off-white `#f7f8fa` |
| Panels | White `#ffffff`, 1 px `#e1e5eb` border, 8 px radius |
| Primary text | Charcoal `#202631` |
| Secondary text | Slate `#5e6979`; minimum 12 px |
| Actions | Blue `#2856cf`; white text |
| States | Green for connected/paired, amber for attention, red for errors/destructive actions |
| Typography | Native system sans-serif; 28–30 px page titles, 16 px section titles, 14 px body |
| Spacing | 20 px mobile page gutters, 24–28 px panel padding, 24–32 px section spacing |
| Controls | At least 44 × 44 px interactive targets; visible keyboard focus |
| Effects | Flat surfaces; no gradients, glows, blur, or decorative charts |

## Responsive structure

- **Below 640 px:** one-column content, vertically grouped metrics, stacked
  forms and action rows, account status below its account label.
- **640–1023 px:** metrics and selected forms gain columns; navigation stays
  behind the hamburger button.
- **1024 px and wider:** persistent 236 px sidebar; account preferences and
  connection details use a fluid two-column grid. Content is capped at 1320 px.
- Mobile navigation is a dismissible modal drawer with Escape, focus wrapping,
  focus restoration, a backdrop, and an inert main region. Switching to desktop
  clears the modal state. Without JavaScript, navigation remains visible.

## Information hierarchy

- **Overview:** concise title and Add account action, one shared summary panel,
  account list, then shared bot status. All numbers come from existing state.
- **Usage:** live resource meters, clearly scoped monthly observations and
  editable free-tier budgets, manual cloud totals and account storage.
- **Account:** name/status and capture controls first; forwarding preferences,
  Telegram destination and an expandable session form follow. Saved cookie
  replacement stays collapsed. Status refresh pauses while details are open.
  Account removal sits separately at the bottom.
- **Setup links:** creation form first; active count and invitation activity
  remain visible. Less frequent operational details use a disclosure.
- **Telegram bot:** token form alongside a short ordered checklist.
- **Guest setup:** matching light theme, three clearly labelled steps, explicit
  remote-browser/session consent, and the existing private Telegram handoff.

## User profiles

The owner portal shares the restrained light palette, typography, responsive
drawer and 44 px controls. Its own navigation contains Overview, Settings,
Conversations, Delivery history and Profile; it does not reuse administrator
navigation or cookie forms. Account state is first in the overview, followed by
retained capture metrics, connection/delivery details and current-run counts.
Snapshots carry their freshness and scope. Profile editing separates a personal
display name from the fixed Messenger/Telegram identity and administrator label.
The sign-in page presents one action, a code comparison, owner approval and an
explicit continue step. User pages and navigation labels follow Polish/English
preferences, with dates explicitly in Europe/Warsaw.

## Mobile remote typing

The labelled keyboard proxy remains focusable but invisible by default on narrow
or coarse-pointer devices. Keyboard, Enter, Done and Typing box remain at least
44 px targets. Revealing the typing box provides a normal visible paste field.
The bounded focus guard and input queue are unchanged by the visual redesign.

## Verification

The browser suite checks the actual Flask pages at 320, 390, 768, 1024 and
1440 px, normal text/button contrast, control geometry, long account labels,
drawer dismissal/focus wrapping, portrait-to-landscape/desktop transitions,
and existing onboarding/typing/capture/approval flows. Screenshots use synthetic
data. Real phone keyboards and live Facebook acceptance require device testing.
