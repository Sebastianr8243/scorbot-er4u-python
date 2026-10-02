# Operator UX: evidence and backlog

Why the bench tools prompt, warn and log the way they do, and what to change next. Sources are listed at the end.

## Principles in use

| Principle | Evidence | Where it shows |
|---|---|---|
| Typed, specific confirmations resist habituation better than y/n | Bravo-Lillo et al. 2014; Anderson et al. 2015 (attention to a repeated warning drops sharply after the second exposure) | `HOME`, `HOME_OK`, `MOVE`; a typo declines, the safe failure |
| Record observations before seeing the expected result | Holman et al. 2015 (non-blind studies report larger effects) | LED prompts never state the expectation and warn only after the answer; the recorder reports physical motion before reading count results |
| Every alarm needs a defined operator action; avoid floods | ISA-18.2 / EEMUA 191 | A decline is `operator_declined` (exit 3, no alarm); `!!!` is reserved for faults and LED mismatches |
| Short checklists with spoken pause points and critical items | Degani & Wiener 1993; WHO Surgical Safety Checklist (Haynes et al. 2009); Gawande | G1 pause points before `HOME` and `MOVE`, with a spoken read-back |
| Situation awareness: perceive, comprehend, project | Endsley 1995; Drury, Scholtz & Yanco (human-robot awareness) | The live view is for the recorder; eyes stay on the arm during motion |
| Structured answers beat free text for later analysis | FAIR principles (Wilkinson et al. 2016) | Single-key LED answers stored as fields |
| CLI conventions | clig.dev; no-color.org | Distinct exit codes: 0 completed, 1 failed, 3 declined |
| A held key must not become a stream of motion | The Windows console reports typed characters, never a key-up; auto-repeat starts after up to 1 s and a quiet gap does not prove release (Codex and Gemini reviews, 2026-10-02) | Teleop waits on the physical key state (`GetAsyncKeyState`) after every step and drops the repeats. The gate only delays motion; motion comes only from a character typed in this console |
| Unattended armed consoles disarm sooner when confirmations are removed | Same reviews | Teleop idle disarm 15 s instead of 60 s; any unknown key disarms |

## Backlog (after G1; each changes the operator flow, so rehearse first)

Items 1-4 and 6 are addressed for the guided session ([LAB_SESSION.md](LAB_SESSION.md)): typed move confirmations, observation before numbers, structured observations, a readable review, and a saved profile instead of flags. The per-script procedure keeps the old prompts.

1. **MOVE confirmation from the plan.** Type the joint and sign shown in the plan (for example `BASE -1`) instead of the fixed word `MOVE`. Split `HOME_OK` into "home looked correct?" and "travel clear?". Drop "(write 'none' if none)", which leads the answer. (S)
2. **Observe before the plan is visible.** Ask the post-jog observation prompts before printing counts, and hide the live view's `plan` and `moved` columns until the operator observation row exists. (M)
3. **Structured observations.** Direction: toward landmark / away / none / unsure; other joint moved: yes / no / unsure, plus text. Makes the review's checks meaningful. (M)
4. **Readable review verdict.** Print `LOG CHECK: N problems (not a safety verdict)` and a planned-vs-observed table first; move the JSON behind `--json`. (S)
5. **Alarm salience in the live view.** A pinned red banner (respecting `NO_COLOR` and non-TTY output), an alarm count, and redrawing in place to reduce flicker in the Windows console. (S)
6. **Less typing, more provenance.** A `--config lab.toml` for fixed labels (robot, arm, controller, driver); record the hostname, OS, Python and package versions, and whether the git tree has uncommitted changes. (M)
7. **One-page do-confirm cards.** Rewrite the G1 checklist as 5-9 critical items per pause point, and move the explanations into `ARM_CONTROL_BENCH.md`. (M)

## Sources

- Degani & Wiener, *Human Factors of Flight-Deck Checklists* (NASA CR-177549); *Human Factors* 35(2), 1993. <https://dx.doi.org/10.1177/001872089303500209>
- Haynes et al., "A Surgical Safety Checklist to Reduce Morbidity and Mortality", *NEJM* 2009. <https://www.nejm.org/doi/full/10.1056/NEJMsa0810119>
- Anderson et al., "How polymorphic warnings reduce habituation in the brain", *CHI* 2015. <https://dl.acm.org/doi/10.1145/2702123.2702322>
- Bravo-Lillo et al., "Harder to Ignore?", *SOUPS* 2014. <https://www.usenix.org/system/files/soups14-paper-bravo-lillo.pdf>
- Holman et al., "Evidence of Experimental Bias in the Life Sciences", *PLOS Biology* 2015. <https://journals.plos.org/plosbiology/article?id=10.1371%2Fjournal.pbio.1002190>
- ISA-18.2 and EEMUA 191 alarm management (overview): <https://www.exida.com/Alarm-Management/Detail/standards_guidelines>
- Endsley, "Toward a Theory of Situation Awareness in Dynamic Systems", *Human Factors* 1995. <https://journals.sagepub.com/doi/10.1518/001872095779049543>
- Drury, Scholtz & Yanco, "Awareness in Human-Robot Interactions" (NIST). <https://www.nist.gov/publications/awareness-human-robot-interactions>
- Wilkinson et al., "The FAIR Guiding Principles", *Scientific Data* 2016. <https://www.nature.com/articles/sdata201618>
- Command Line Interface Guidelines: <https://github.com/cli-guidelines/cli-guidelines>; NO_COLOR: <https://no-color.org/>
