"""Small local agent swarm. One model, several agents taking turns."""
import json
import queue
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import memory
import project_files

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.6:27b"
ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
MAX_ROUNDS = 3
COUNCIL = (
    ("mira", "Mira", "You argue for the simplest route that can actually work."),
    ("sol", "Sol", "You argue for the most reliable route and name what can break."),
    ("nia", "Nia", "You argue for a less obvious route only when it has a real advantage."),
)
BUILDERS = (
    ("builder-small", "Wren", "You want the smallest set of files that can run and be checked."),
    ("builder-sure", "Otto", "You want each file to have one job, and a check that fails when that job breaks."),
    ("builder-fit", "Pia", "You want the build to follow files already in the folder when they are a good fit."),
)
TESTERS = (
    ("tester-break", "Reed", "You look for what will break when this is run."),
    ("tester-miss", "Nell", "You look for behavior the request asked for that the files still leave out."),
    ("tester-run", "Ivo", "You read the check output and say what actually ran and what failed."),
)


class Swarm:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.history = []
        self.listeners = []
        self._lesson_block = ""
        self._answers = queue.Queue()
        self._stop = False

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            backlog = list(self.history)
            self.listeners.append(q)
        return q, backlog

    def unsubscribe(self, q):
        with self._lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def publish(self, event):
        with self._lock:
            if event.get("kind") != "token":
                self.history.append(event)
            listeners = list(self.listeners)
        for q in listeners:
            q.put(event)

    def guide(self, text):
        """A waiting question reads the queue. A written fix is also kept for the builder."""
        text = text or ""
        fix = injected_fix(text)
        if fix:
            self._user_fix = fix
            self._fix_built = ""
        self._answers.put(text)

    def request_stop(self):
        self._stop = True
        self._answers.put("stop")
        cancel_streams()

    def start(self, problem, team_name="", members=None, project_mode=False, project_name="", yolo=False):
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.history = []
            self._stop = False
            self._user_fix = ""
            self._fix_built = ""
            while not self._answers.empty():
                try:
                    self._answers.get_nowait()
                except queue.Empty:
                    break
        threading.Thread(
            target=self._run,
            args=(problem, team_name, members or [], bool(project_mode), project_name or "", bool(yolo)),
            daemon=True,
        ).start()
        return True

    def _run(self, problem, team_name, members, project_mode=False, project_name="", yolo=False):
        try:
            team_name = clean_name(team_name, "Council")
            names = unique_names([
                clean_name(members[i] if i < len(members) else "", COUNCIL[i][1])
                for i in range(len(COUNCIL))
            ])
            council = tuple(
                (COUNCIL[i][0], names[i], COUNCIL[i][2]) for i in range(len(COUNCIL))
            )
            roster = ", ".join(names)
            root = project_files.project_dir(project_name) if project_mode else ""
            lessons = memory.retrieve(problem, project=str(root) if root else "")
            if project_mode:
                lessons = [
                    item for item in lessons
                    if not hands_off(item.get("lesson", ""), "")
                    and not blocks_fix(item.get("lesson", ""))
                ]
            self._lesson_block = memory.prompt_block(lessons)
            self.publish({"kind": "run", "state": "working", "team": team_name, "members": names})
            self.publish({
                "kind": "memory",
                "lessons": [memory.show_lesson(item) for item in lessons],
            })
            transcript = []
            note = "No one has spoken yet. Each of you propose a route."
            route = ""
            agreed = False

            for round_n in range(1, MAX_ROUNDS + 1):
                self.publish({"kind": "round", "label": "Round %s — answering each other" % round_n})
                for agent_id, name, stance in council:
                    heard = render_transcript(transcript)
                    text = self._speak(
                        agent_id,
                        name,
                        "You are %s on the team %s, with %s. %s "
                        "Speak to the others by name. If someone else's route is good enough, "
                        "adopt it instead of starting a new one. "
                        "If the user asked to complete, fill, finish, or fix a file, the route edits that file and runs it. "
                        "Do not agree to static analysis or to leaving the files unchanged. "
                        "Use this shape:\n"
                        "ROUTE: one sentence\nSAY: two to four sentences\nAGREE: yes or no"
                        % (name, team_name, roster, stance),
                        "Problem:\n%s\n\nChair:\n%s\n\nCouncil so far:\n%s"
                        % (problem, note, heard or "(nothing yet)"),
                        260,
                        temperature=0.6,
                        continued=round_n > 1,
                    )
                    transcript.append((round_n, name, text))

                decision = self._speak(
                    "chair",
                    "Chair",
                    "You chair the team %s (%s). They are trying to agree on one route. "
                    % (team_name, roster) +
                    "Say they agree only when one route is clearly shared. Otherwise name "
                    "the single disagreement they must answer next. "
                    "If the user asked to complete, fill, finish, or fix a file, agree a route that edits that file. "
                    "Do not agree to static analysis or to leaving the files unchanged. Use this shape:\n"
                    "AGREED: yes or no\nROUTE: one sentence\nASK: one sentence",
                    "Problem:\n%s\n\nCouncil:\n%s" % (problem, render_transcript(transcript)),
                    200,
                    temperature=0.2,
                    continued=round_n > 1,
                )
                transcript.append((round_n, "Chair", decision))
                agreed, route, note = parse_decision(decision)
                self.publish({
                    "kind": "decision",
                    "agreed": agreed,
                    "route": route,
                    "note": note,
                })
                if agreed and route:
                    break
                note = note or "Answer the remaining disagreement and converge."

            if not route:
                route = last_route(transcript) or note or "Use the council's last concrete proposal."
            fix = (getattr(self, "_user_fix", "") or "").strip()
            if fix:
                route = fix
                self.publish({
                    "kind": "note",
                    "text": "Using the fix you wrote.",
                })
            elif work_requested(problem) and study_route(route):
                route = "Edit the unfinished file named in the request, then run it."
                self.publish({
                    "kind": "note",
                    "text": "The route only studied the files. The request was to finish them, so the build will edit and run.",
                })

            if project_mode and not hands_off(problem, route):
                built, result_note, passed = self._build_test_fix(
                    problem, route, transcript, council, project_name, yolo
                )
                latest_answer = built
                latest_output = result_note
                if passed:
                    latest_output = "Tests passed.\n" + result_note
                elif (result_note or "").startswith("Stopped"):
                    latest_output = result_note
                else:
                    latest_output = "Tests were still failing after the fix passes.\n" + result_note
            elif hands_off(problem, route):
                self.publish({"kind": "round", "label": "They studied it and left the files alone"})
                latest_answer = route
                latest_output = route
                result_note = route
            else:
                self.publish({"kind": "round", "label": "Building the agreed route"})
                built, result_note, output = self._build(
                    self._builder_system(False),
                    "Problem:\n%s\n\nAgreed route:\n%s\n\nCouncil:\n%s"
                    % (problem, route, render_transcript(transcript)),
                    800,
                    False,
                    project_name,
                )
                latest_answer = built
                latest_output = output or ""
                result_note = output or built
                self.publish({"kind": "round", "label": "The council checks the result"})
                objections = []
                for agent_id, name, stance in council:
                    reaction = self._speak(
                        agent_id,
                        name,
                        "You are %s. The builder followed the agreed route. "
                        "Say whether the result matches it, and answer the others. "
                        "Use this shape:\nMATCH: yes or no\nSAY: two or three sentences"
                        % name,
                        "Agreed route:\n%s\n\nResult:\n%s" % (route, result_note[:4000]),
                        180,
                        temperature=0.4,
                        continued=True,
                    )
                    objections.append("%s: %s" % (name, reaction))
                verdict = self._speak(
                    "chair",
                    "Chair",
                    "You decide if the builder must revise once. Revise only if the result "
                    "misses the agreed route. Use this shape:\nREVISE: yes or no\nWHY: one sentence",
                    "Agreed route:\n%s\n\nResult:\n%s\n\nCouncil:\n%s"
                    % (route, result_note[:4000], "\n".join(objections)),
                    160,
                    temperature=0.2,
                    continued=True,
                )
                revise = field(verdict, "REVISE").lower().startswith("y")
                self.publish({
                    "kind": "decision",
                    "agreed": not revise,
                    "route": route,
                    "note": field(verdict, "WHY") or verdict,
                })
                if revise:
                    self.publish({"kind": "round", "label": "Revising from the council's objections"})
                    revised, _note, revised_output = self._build(
                        self._builder_system(False, revise=True),
                        "Agreed route:\n%s\n\nPrevious result:\n%s\n\nObjections:\n%s"
                        % (route, result_note[:3000], "\n".join(objections)),
                        700,
                        False,
                        project_name,
                        continued=True,
                    )
                    latest_answer = revised
                    latest_output = revised_output

            final_text = collective_text(team_name, route, latest_answer, latest_output)
            self.publish({"kind": "final", "text": final_text})
            self.publish({"kind": "round", "label": "Saving what worked on this PC"})
            try:
                lesson = memory.remember(
                    problem, final_text, "swarm", project=str(root) if root else ""
                )
            except Exception:
                lesson = None
            self.publish({"kind": "memory_saved", "lesson": lesson or ""})
            self.publish({"kind": "run", "state": "done"})
        except Exception as exc:
            self.publish({"kind": "error", "text": str(exc)})
            self.publish({"kind": "run", "state": "done"})
        finally:
            with self._lock:
                self.running = False

    def _build_test_fix(self, problem, route, transcript, council, project_name, yolo):
        """Plan the files, write them one at a time, then patch until the checks pass."""
        root = project_files.project_dir(project_name)
        self._problem = problem
        self._plan = self._agree_plan(council, problem, route, root, yolo)
        if not self._plan or self._stop:
            return "", "Stopped before the builders agreed on the files.", False
        built = self._write_planned(self._plan, problem, route, root, project_name, council)
        summary = "Files: %s" % ", ".join(self._plan)
        check_output = ""
        edit_notes = ""
        passed = False
        cycle = 0
        self._seen_failures = set()
        self._direct_keys = set()
        self._install_tried = set()
        self._refinished = set()
        self._cause_handled = False
        while not self._stop:
            cycle += 1
            if yolo and cycle > 25:
                self.publish({"kind": "round", "label": "Still failing after 25 passes"})
                break
            before = project_files.snapshot_project(root)
            passed, check_output = self._check_project(root)
            self.publish({
                "kind": "test",
                "ok": passed,
                "cycle": cycle,
                "text": check_output,
            })
            self.publish({
                "kind": "output",
                "id": "tester-run",
                "name": "Ivo",
                "text": check_output or "No check output.",
            })
            if passed:
                self._review_passed(problem, route, check_output)
                self.publish({"kind": "round", "label": "Tests passed"})
                self._keep_what_worked(root, problem, route or summary, before, project_name)
                break
            self._lesson_block = memory.prompt_block(
                memory.retrieve(problem, project=str(root), failure=check_output)
            )
            if self._stop:
                break
            fix = (getattr(self, "_user_fix", "") or "").strip()
            fresh_fix = bool(fix) and fix != getattr(self, "_fix_built", "")
            spoken = ""
            issues = []
            if fresh_fix:
                self._fix_built = fix
                route = fix
                spoken = fix
                self.publish({
                    "kind": "decision",
                    "agreed": True,
                    "route": route,
                    "note": "Following the fix you wrote. The council will not replace it.",
                })
            else:
                if fix:
                    route = fix
                if self._environment_blocked(check_output, root):
                    if self._fix_from_cause(root, check_output):
                        continue
                aligned = project_files.align_names(root, check_output)
                aligned.extend(project_files.obvious_fixes(root, check_output))
                if aligned:
                    for event in aligned:
                        published = dict(event)
                        published.setdefault("id", "builder-small")
                        published.setdefault("name", "Wren")
                        self.publish(published)
                    self.publish({"kind": "note", "text": "Updated the names to match what the check found."})
                    continue
                if not fix and self._refinish_notebooks(problem, route, root, project_name, check_output):
                    continue
                key = failure_key(check_output)
                if key and key not in self._direct_keys and not fix:
                    self._direct_keys.add(key)
                    self._direct_patch(problem, route, root, project_name, check_output)
                    if self._stop:
                        break
                    continue
                if yolo:
                    key = failure_key(check_output)
                    seen = self._seen_failures
                    looping = bool(key) and key in seen
                    if key:
                        seen.add(key)
                    if looping:
                        steer = self._ask_about_loop(check_output)
                        if steer == "stop" or self._stop:
                            self.publish({"kind": "round", "label": "Stopped so you can take over"})
                            break
                        if steer:
                            spoken = steer
                            route = steer
                            self._user_fix = injected_fix(steer) or self._user_fix
                            self.publish({"kind": "note", "text": "You said: " + steer})
                pending = getattr(self, "_steer", "")
                if pending:
                    spoken = pending
                    route = pending
                    self._steer = ""
                    fixed = injected_fix(pending)
                    if fixed:
                        self._user_fix = fixed
                        self._fix_built = ""
                    self.publish({"kind": "note", "text": "You said: " + pending})
                if fix and not spoken:
                    spoken = fix
                    route = fix
            if spoken and not fresh_fix:
                self.publish({
                    "kind": "decision",
                    "agreed": True,
                    "route": route,
                    "note": "Following what you wrote. The council will not replace it.",
                })
            else:
                issues = self._report_issues(problem, route, root, check_output)
                if self._stop:
                    break
                new_route = self._route_from_issues(council, problem, route, issues, check_output, yolo, root)
                new_route = self._keep_route(new_route, check_output, root)
                if self._stop or not new_route or new_route.strip().lower() in ("stop", "quit", "cancel"):
                    self.publish({"kind": "round", "label": "Stopped so you can take over"})
                    break
                route = new_route
                self.publish({
                    "kind": "decision",
                    "agreed": True,
                    "route": route,
                    "note": "The council set a new route for the testers' issues.",
                })
            self.publish({"kind": "round", "label": "Building the new route (%s)" % cycle})
            user_fix = (getattr(self, "_user_fix", "") or "").strip()
            if user_fix:
                self._fix_built = user_fix
                route = user_fix
            before_cells = project_files.snapshot_project(root)
            if not user_fix and self._fill_agreed_notebooks(problem, route, root, project_name, check_output):
                if project_files.snapshot_project(root) != before_cells:
                    self.publish({
                        "kind": "note",
                        "text": "The agreed route was written into the notebook.",
                    })
                    continue
            context = self._context(root, check_output, problem)
            brief = project_files.failure_brief(root, check_output)
            issue_text = "\n".join("%s: %s" % (item["name"], item["text"]) for item in issues)
            user = (
                "Problem:\n%s\n\nWhat the traceback shows:\n%s\n\nNew route from the council:\n%s\n\n"
                "Issues from the testers:\n%s\n\nPlanned files:\n%s\n\n%s"
                % (
                    problem,
                    brief or "Use the traceback, not a guess about an empty cell.",
                    route,
                    issue_text or "(none)",
                    "\n".join(self._plan),
                    context,
                )
            )
            if user_fix:
                user = (
                    "The user injected this fix. Apply it on this edit. "
                    "It overrides the council, the check's guess, and any note that says to study the files or not to run them.\n"
                    "%s\n\n%s" % (user_fix, user)
                )
            user += "\n\nThe last run failed. Change the code so it follows the new route.\nTest output:\n%s" % check_output[:4000]
            if edit_notes:
                user += "\n\nLast edit:\n%s" % edit_notes
            before = project_files.snapshot_project(root)
            piece, summary, edit_notes = self._build(
                self._builder_system(True, revise=True),
                user,
                2400,
                True,
                project_name,
                continued=True,
                think=True,
            )
            if piece:
                built = piece
            if self._stop:
                break
            if project_files.snapshot_project(root) == before:
                self.publish({
                    "kind": "note",
                    "text": "That reply did not change the code. Writing the agreed route into the file.",
                })
                self._force_agreed_write(problem, route, root, project_name, check_output)
        note = (summary or "") + "\n\n" + (check_output or "")
        if passed:
            note = "Tests passed on cycle %s.\n%s" % (cycle, note)
        elif self._stop:
            note = "Stopped before the tests passed.\n%s" % note
        else:
            note = "Tests were still failing after %s cycles.\n%s" % (cycle, note)
        return built, note, passed

    def _agree_plan(self, council, problem, route, root, yolo):
        """Each builder names a file list, then one list is kept."""
        self.publish({"kind": "round", "label": "The builders are choosing the files"})
        existing = project_files.list_project(root)
        proposals = []
        heard = []
        for agent_id, name, stance in BUILDERS:
            if self._stop:
                return []
            text = self._speak(
                agent_id,
                name,
                "You are %s, a builder on this team. %s "
                "Name a file list that can differ from the builders who already spoke. "
                "%s"
                "If a notebook is already in the folder, keep it on the list so its unfinished cells can be completed. "
                "Do not add a Solved folder or a solution notebook. If none is in the folder, it is not part of the work. "
                "Do not write file contents. Use this shape:\n"
                "PLAN:\n"
                "one relative path per line\n"
                "SAY: two sentences"
                % (name, stance, _plan_rule(problem, root)),
                "Problem:\n%s\n\nAgreed route:\n%s\n\nAlready in the folder:\n%s\n\n"
                "Other builders:\n%s"
                % (problem, route, existing or "(empty)", "\n\n".join(heard) or "(none yet)"),
                320,
                temperature=0.5,
            )
            plan = project_files.parse_plan(text) or project_files.fallback_plan(problem, root)
            say = field(text, "SAY") or (text or "").strip()[:400]
            proposals.append({"id": agent_id, "name": name, "text": say, "plan": plan})
            heard.append("%s: %s\n%s" % (name, say, "\n".join(plan)))
        if self._stop or not proposals:
            return []
        decision = self._speak(
            "builder-chair",
            "Chair",
            "You chair the builders. They each proposed a file list. Keep one shared build. "
            "%s"
            "Leave off a Solved folder or a solution notebook when that file is not already in the folder. "
            "Use this shape:\n"
            "KEEP: the builder's name\nPLAN:\none path per line\nSAY: one sentence"
            % _chair_plan_rule(problem, root),
            "Problem:\n%s\n\nRoute:\n%s\n\nBuilders:\n%s"
            % (problem, route, "\n\n".join(heard)),
            280,
            temperature=0.2,
        )
        plan = project_files.parse_plan(decision) or proposals[0]["plan"]
        note = field(decision, "SAY") or "The builders settled on one file list."
        self._remember_writer(proposals, field(decision, "KEEP") or decision, prefer_name=True)
        if not yolo:
            choice = self._ask_options(
                "The builders each planned a different build. Pick one, or write the file list you want.",
                "\n\n".join(heard),
                [{"name": item["name"], "text": item["text"] + "\n" + ", ".join(item["plan"])} for item in proposals],
            )
            if self._stop or (choice or "").strip().lower() in ("stop", "quit", "cancel"):
                return []
            plan = choose_plan(proposals, choice)
            note = choice
            self._remember_writer(proposals, choice, prefer_name=False)
        plan, dropped = project_files.drop_missing_answers(plan, root)
        if dropped:
            note = (note or "") + " No solved copy in this project, so " + ", ".join(dropped) + " stays off the list."
        plan, added = project_files.include_unfinished(plan, root)
        if added:
            note = (note or "") + " Also finishing " + ", ".join(added) + "."
        plan, extras = project_files.drop_notebook_extras(plan, root, problem)
        if extras:
            note = (note or "") + " The notebook is the check, so " + ", ".join(extras) + " stays off the list."
        _plan, swapped = project_files.use_existing_data(plan, root)
        plan = _plan
        if swapped:
            note = (note or "") + " Using " + ", ".join(new + " instead of " + old for old, new in swapped) + "."
        self.publish({"kind": "plan", "files": plan})
        self.publish({"kind": "note", "text": note})
        return plan

    def _remember_writer(self, proposals, guidance, prefer_name=False):
        """The builder who writes is the one who was named, otherwise the first."""
        named = match_named(proposals, guidance)
        use_named = named and (prefer_name or (guidance or "").lower().lstrip().startswith("use "))
        chosen = named if use_named else (proposals[0] if proposals else None)
        if chosen:
            self._writer = (chosen["id"], chosen["name"])

    def _review_passed(self, problem, route, check_output):
        """Testers say what they checked once the run has already passed."""
        self.publish({"kind": "round", "label": "The testers are checking the build"})
        for agent_id, name, stance in TESTERS:
            if self._stop:
                return
            self._speak(
                agent_id,
                name,
                "You are %s, a tester. The checks passed. %s "
                "Do not ask for a rewrite. Use this shape:\n"
                "VERDICT: pass\nFIND: one sentence on what you checked"
                % (name, stance),
                "Problem:\n%s\n\nAgreed route:\n%s\n\nCheck output:\n%s"
                % (problem, route, (check_output or "")[:2000]),
                140,
                temperature=0.3,
            )
        if self._stop:
            return
        self._speak(
            "tester-chair",
            "Chair",
            "You chair the testers. The checks passed. Say what the group verified. "
            "Do not ask for a rewrite. Use this shape:\n"
            "VERDICT: pass\nFIND: one sentence",
            "Problem:\n%s\n\nAgreed route:\n%s\n\nCheck output:\n%s"
            % (problem, route, (check_output or "")[:2000]),
            140,
            temperature=0.2,
        )

    def _ask_options(self, question, detail, options):
        self.publish({
            "kind": "ask",
            "question": question,
            "detail": detail,
            "options": options,
        })
        try:
            return self._answers.get(timeout=6 * 60 * 60)
        except queue.Empty:
            return "stop"

    def _install_set(self):
        tried = getattr(self, "_install_tried", None)
        if tried is None:
            tried = set()
            self._install_tried = tried
        return tried

    def _environment_blocked(self, output, root):
        """True when the check still has an environment fix that has not been tried."""
        info = project_files.classify_failure(output, root, self._install_set())
        return info["kind"] in project_files._ENV_KINDS and bool(info.get("action"))

    def _refinish_notebooks(self, problem, route, root, project_name, check_output):
        """Fill unfinished cells and let the next run judge the code. A repeated read error does not block that."""
        if "Unfinished code:" not in (check_output or "") and not project_files.unfinished_notebooks(root):
            return False
        if project_files.missing_packages(check_output, root):
            return False
        tries = getattr(self, "_refill_tries", None)
        if tries is None:
            tries = {}
            self._refill_tries = tries
        before = project_files.snapshot_project(root)
        touched = False
        rels = list(getattr(self, "_plan", None) or [])
        for rel in project_files.unfinished_notebooks(root):
            if rel not in rels:
                rels.append(rel)
        for rel in rels:
            if not str(rel).lower().endswith(".ipynb") or project_files.is_answer_key(rel):
                continue
            if tries.get(rel, 0) >= 4:
                continue
            try:
                path = project_files.safe_path(root, rel)
            except ValueError:
                continue
            if not path.is_file() or not project_files.source_gaps(path):
                continue
            tries[rel] = tries.get(rel, 0) + 1
            self._finish_notebook_cells(
                rel, path, problem, route, root, project_name, check_output
            )
            touched = True
        if not touched:
            return False
        return project_files.snapshot_project(root) != before

    def _install_private(self, root, packages, why, version=False, repair=False, modules=None):
        """Install into this project's environment. A deep folder already has its short environment."""
        names = ", ".join(packages)
        if version:
            question = (
                "Install " + names + " into this project's environment? "
                "The copy there does not include the module the notebook imports."
            )
            yes = "Install " + names + " and continue."
            label = "Asking before installing the version that includes that module"
        else:
            question = yes = label = None
        if not self._allow_install(packages, why, repair=repair, question=question, yes=yes, label=label):
            return False
        place = "short" if project_files.path_too_deep(root) else "project"
        self.publish({"kind": "round", "label": ("Reinstalling " if repair else "Installing ") + names})
        ok, log = project_files.install_packages(
            root, packages, repair=repair, place=place, check_modules=modules
        )
        self.publish({"kind": "note", "text": log})
        return bool(ok) and not self._stop

    def _act_on_cause(self, root, output):
        """Carry out the one environment action that matches this check. Never repeat it."""
        tried = self._install_set()
        info = project_files.classify_failure(output, root, tried)
        action = info.get("action")
        packages = info.get("packages") or []
        if not action or not packages:
            return False, ""
        tried.add(action.lower())
        for name in packages:
            base = project_files._pip_base(name).lower()
            tried.add(name.lower())
            if info["kind"] == "repair" and base:
                tried.add("repair:" + base)
            if info["kind"] == "missing" and base:
                tried.add("install:" + base)
                tried.add(base)
        self.publish({"kind": "note", "text": info["cause"]})
        ok = self._install_private(
            root,
            packages,
            info["cause"],
            version=info["kind"] == "missing_submodule",
            repair=bool(info.get("repair")),
            modules=project_files.modules_mentioned(output),
        )
        return bool(ok), "" if ok else info["cause"]

    def _fix_from_cause(self, root, check_output):
        """Install or repair what the check is actually missing. A path fix is not reused for a later error."""
        ok, _note = self._act_on_cause(root, check_output)
        return ok

    def _offer_install(self, root, output):
        """Ask once for the environment action this check needs."""
        return self._act_on_cause(root, output)

    def _fix_prefix(self):
        """The injected fix, placed ahead of the check so the builder applies it."""
        fix = (getattr(self, "_user_fix", "") or "").strip()
        if not fix:
            return ""
        return (
            "The user injected this fix. Apply it. "
            "It overrides the council, the check's guess, and any note that says to study the files or not to run them.\n"
            "%s\n\n" % fix
        )

    def _keep_route(self, route, output, root):
        """Drop a route that repeats an install or a path fix the check does not ask for."""
        if (route or "").strip() and (route or "").strip() == (getattr(self, "_user_fix", "") or "").strip():
            return route
        info = project_files.classify_failure(output, root, self._install_set())
        if project_files.route_fits(route, info):
            return route
        replacement = project_files.code_route(info)
        self.publish({
            "kind": "note",
            "text": "That route does not match the check. " + replacement,
        })
        return replacement

    def _allow_install(self, packages, why, repair=False, question=None, yes=None, label=None):
        """Ask before installing. Yes installs. No continues without them."""
        names = ", ".join(packages)
        key = tuple(sorted(name.lower() for name in packages))
        declined = getattr(self, "_declined_installs", None)
        if declined is None:
            declined = set()
            self._declined_installs = declined
        if key in declined:
            self.publish({"kind": "note", "text": "Skipping " + names + ". You already said not to install them."})
            return False
        if question is None and repair:
            question = "Reinstall " + names + "? The copy in this project is incomplete."
            yes = "Remove the broken copy and install " + names + " again."
            label = "Asking before reinstalling " + names
        elif question is None:
            question = "Install " + names + " into this project before continuing?"
            yes = "Install " + names + " and continue."
            label = "Asking before installing " + names
        self.publish({"kind": "round", "label": label})
        choice = self._ask_options(
            question,
            (why or "These packages are needed to run the checks.")[:2000],
            [
                {"name": "Yes", "text": yes},
                {"name": "No", "text": "Do not install. Continue without them."},
            ],
        )
        kind = answer_kind(choice)
        if kind == "stop" or self._stop:
            self._stop = True
            return False
        if kind == "yes":
            return True
        if kind == "text" and (choice or "").strip():
            self.publish({"kind": "note", "text": "You said: " + choice.strip()})
            self._steer = choice.strip()
        declined.add(key)
        return False

    def _ask_about_loop(self, check_output):
        """YOLO still stops when the same failure comes back."""
        self.publish({"kind": "round", "label": "This problem is coming back"})
        choice = self._ask_options(
            "This same problem is back. Keep going, stop, or write how you want it handled.",
            (check_output or "")[:2000],
            [
                {"name": "Keep going", "text": "Let them try another route."},
                {"name": "Stop", "text": "Stop here so you can take over."},
            ],
        )
        kind = answer_kind(choice)
        if kind == "stop" or self._stop or kind == "no":
            return "stop"
        if kind in ("continue", "yes"):
            return ""
        return (choice or "").strip()

    def _agree_file(self, council, relative, problem, route, root, excerpt=""):
        """Each builder says what this file should contain, then the chair keeps one shape."""
        self.publish({"kind": "round", "label": "The builders are deciding %s" % relative})
        heard = []
        folder = project_files.list_project(root) or "(empty)"
        current = excerpt or "(new file)"
        for agent_id, name, stance in BUILDERS:
            if self._stop:
                return ""
            text = self._speak(
                agent_id,
                name,
                "You are %s, a builder. %s You are deciding %s only. "
                "%s "
                "If the file already has cells, say which ones to keep and which ones to finish. "
                "Do not write the file. Use this shape:\n"
                "BUILD: two or three sentences on what this file must contain"
                % (name, stance, relative, project_files.language_for(relative)),
                "Problem:\n%s\n\nAgreed route:\n%s\n\nFiles already here:\n%s\n\n"
                "Current file:\n%s\n\n"
                "Other builders on this file:\n%s"
                % (problem, route, folder, current, "\n".join(heard) or "(none yet)"),
                220,
                temperature=0.5,
            )
            opinion = field(text, "BUILD") or (text or "").strip()[:400]
            heard.append("%s: %s" % (name, opinion))
        if self._stop:
            return ""
        decision = self._speak(
            "builder-chair",
            "Chair",
            "You chair the builders. Agree on one shape for this file. The writer will follow it "
            "and write nothing else. Keep cells that already work, and name the cells that still "
            "need code. Use this shape:\nSHAPE: three sentences",
            "File: %s\n\nCurrent file:\n%s\n\nBuilders:\n%s" % (relative, current, "\n".join(heard)),
            220,
            temperature=0.2,
        )
        shape = field(decision, "SHAPE") or (decision or "").strip()[:500]
        self.publish({"kind": "note", "text": "Agreed shape for %s. %s" % (relative, shape)})
        return shape

    def _agreed_target(self, root, check_output):
        """The source file this agreement has to change."""
        gaps = project_files.unfinished_sources(root)
        if gaps:
            return gaps[0]
        root_path = Path(root).resolve()
        named = []
        for match in re.finditer(r'File "([^"]+)"', check_output or ""):
            candidate = Path(match.group(1))
            try:
                rel = candidate.resolve().relative_to(root_path).as_posix()
            except (OSError, ValueError):
                continue
            if project_files.is_answer_key(rel):
                continue
            if candidate.suffix.lower() in {".csv", ".tsv", ".md", ".txt"}:
                continue
            named.append(rel)
        for rel in named:
            name = Path(rel).name.lower()
            if not name.startswith("test_") and not name.endswith("_test.py"):
                return rel
        if named:
            return named[0]
        for rel in getattr(self, "_plan", None) or []:
            if project_files.is_answer_key(rel):
                continue
            if Path(rel).suffix.lower() in {".csv", ".tsv", ".md", ".txt"}:
                continue
            return rel
        return ""

    def _force_agreed_write(self, problem, route, root, project_name, check_output):
        """The agreed route is not done until a source file changes."""
        rel = self._agreed_target(root, check_output)
        if not rel or self._stop:
            return
        self.publish({"kind": "round", "label": "Writing the agreed route into %s" % rel})
        before = project_files.snapshot_project(root)
        if str(rel).lower().endswith(".ipynb"):
            try:
                path = project_files.safe_path(root, rel)
            except ValueError:
                path = None
            if path is not None and path.is_file():
                self._finish_notebook_cells(
                    rel, path, problem, route, root, project_name, check_output
                )
                if project_files.snapshot_project(root) != before or self._stop:
                    return
        excerpt = self._file_excerpt(root, rel)
        self._build(
            "You are the builder. The last reply changed no file. "
            "Write the agreed route into %s now. Start with PATCH or FILE. Do not explain. "
            "%s "
            "Finish the unfinished code in that file. Do not create a Solved folder."
            % (rel, project_files.language_for(rel)),
            "%sAgreed route:\n%s\n\nCheck:\n%s\n\nCurrent file:\n%s"
            % (
                self._fix_prefix(),
                (route or "")[:800],
                project_files.focus_failure(check_output),
                excerpt or "(empty)",
            ),
            2000,
            True,
            project_name,
            continued=True,
            think=False,
        )

    def _fill_agreed_notebooks(self, problem, route, root, project_name, check_output=""):
        """An agreed route fills the unfinished notebook cells. It does not stop at the sentence."""
        found = False
        rels = list(getattr(self, "_plan", None) or [])
        for rel in project_files.unfinished_notebooks(root):
            if rel not in rels:
                rels.append(rel)
        for rel in rels:
            if not str(rel).lower().endswith(".ipynb") or project_files.is_answer_key(rel):
                continue
            try:
                path = project_files.safe_path(root, rel)
            except ValueError:
                continue
            if not path.is_file() or not project_files.source_gaps(path):
                continue
            found = True
            self._finish_notebook_cells(
                rel, path, problem, route, root, project_name, check_output
            )
        return found

    def _finish_notebook_cells(self, relative, path, problem, route, root, project_name, check_output=""):
        """Write one unfinished cell at a time, using the comment in that cell as the task."""
        agent_id, name = getattr(self, "_writer", None) or ("builder-small", "Wren")
        columns = project_files.work_hint(root, check_output)
        jobs = project_files.notebook_jobs(path)
        earlier = []
        for job in jobs:
            source = job["source"]
            if not job["finish"]:
                if source.strip():
                    earlier.append("Cell %s:\n%s" % (job["index"], source[:500]))
                continue
            if self._stop:
                return
            try:
                self._finish_one_cell(
                    job, relative, path, problem, route, root, columns, earlier, agent_id, name
                )
            except RuntimeError:
                raise
            except Exception as exc:
                if self._stop:
                    return
                self.publish({
                    "kind": "note",
                    "text": "Cell %s was left as it is. %s" % (job["index"], exc),
                })

    def _finish_one_cell(self, job, relative, path, problem, route, root, columns, earlier, agent_id, name):
        """Write one cell. A failure here must not end the rest of the notebook."""
        source = job["source"]
        self.publish({
            "kind": "round",
            "label": "Writing cell %s of %s" % (job["index"], relative),
        })
        reply = self._cell_reply(agent_id, name, problem, route, columns, earlier, source, "", path)
        code = self._cell_code(reply, source)
        if not code and not self._stop:
            reply = self._cell_reply(
                agent_id, name, problem, route, columns, earlier, source,
                "The last reply was not code. Return only the Python for this cell.",
                path,
            )
            code = self._cell_code(reply, source)
        if not code:
            self.publish({"kind": "note", "text": "Cell %s was left as it is." % job["index"]})
            return
        code = project_files.retarget_reads(code, root)
        if project_files._sham_body(code):
            self.publish({
                "kind": "note",
                "text": "Cell %s was left as it is. That reply only pretended to finish." % job["index"],
            })
            return
        pieces = [item.split(":\n", 1)[-1] for item in earlier] + [code]
        ran, error = project_files.try_notebook_cells(root, path, pieces)
        if not ran and not self._stop and project_files._READ_FAIL.search(error or ""):
            aimed = project_files.retarget_reads(code, root)
            if aimed != code:
                code = aimed
                pieces = [item.split(":\n", 1)[-1] for item in earlier] + [code]
                ran, error = project_files.try_notebook_cells(root, path, pieces)
        if not ran and not self._stop:
            ran, error = self._install_for_cell(root, path, pieces, error)
        if not ran and not self._stop:
            self.publish({"kind": "note", "text": "Cell %s did not run. Trying once more." % job["index"]})
            reply = self._cell_reply(
                agent_id, name, problem, route, columns, earlier, source, _text_tail(error, 800), path
            )
            code = project_files.retarget_reads(self._cell_code(reply, source) or code, root)
            if project_files._sham_body(code):
                self.publish({
                    "kind": "note",
                    "text": "Cell %s was left as it is. That reply only pretended to finish." % job["index"],
                })
                return
            pieces = [item.split(":\n", 1)[-1] for item in earlier] + [code]
            ran, error = project_files.try_notebook_cells(root, path, pieces)
        if not _cell_compiles(code):
            self.publish({
                "kind": "note",
                "text": "Cell %s was left as it is. The reply was not valid Python." % job["index"],
            })
            return
        if not ran:
            self.publish({
                "kind": "note",
                "text": "Cell %s was written. The trial run failed: %s" % (job["index"], _last_line(error)),
            })
        if project_files.write_notebook_cell(path, job["index"], code):
            self.publish({
                "kind": "file",
                "id": agent_id,
                "name": name,
                "path": str(path),
                "code": code if len(code) < 2000 else code[:2000] + "\n...",
                "patched": True,
            })
            earlier.append("Cell %s:\n%s" % (job["index"], code[:500]))

    def _install_for_cell(self, root, path, pieces, error):
        """A missing package is an install question, not a reason to rewrite the cell."""
        ok, log = self._offer_install(root, error)
        if not ok:
            if log:
                return False, ((error or "") + "\n" + log).strip()
            return False, error
        return project_files.try_notebook_cells(root, path, pieces)

    def _cell_reply(self, agent_id, name, problem, route, columns, earlier, source, error, path):
        extra = ""
        if error:
            extra = "\n\nThe last try failed:\n" + error
        return self._speak(
            agent_id,
            name,
            "You write one notebook cell. The agreed route is the work to put in this cell. "
            "The comments show where that work goes. "
            "Write code that can be run. If a data file did not open, read a file that is listed below. "
            "Do not create a placeholder file, and do not answer by explaining the failed read or the empty cell. "
            "Use the column names exactly as they are given. "
            "Use names defined in earlier cells. "
            "%s "
            "Return only the code for this one cell. Plain Python is added under the comments. "
            "A code fence is optional. Do not return the file's JSON wrapper. "
            "Keep the comment lines. Do not write a CELL heading, and do not write shell lines."
            % project_files.language_for(path),
            "%sProblem:\n%s\n\nRoute:\n%s\n\n%s\n\nEarlier cells:\n%s\n\nThis cell:\n%s%s"
            % (
                self._fix_prefix(),
                (problem or "")[:1000],
                (route or "")[:600],
                columns,
                "\n\n".join(earlier[-4:]) or "(none)",
                source or "(empty cell)",
                extra,
            ),
            800,
            temperature=0.2,
            think=False,
        )

    def _cell_code(self, reply, source):
        return project_files.cell_code_from_reply(reply, source)

    def _file_excerpt(self, root, relative):
        try:
            path = project_files.safe_path(root, relative)
            text, _encoding = project_files.read_source(path)
        except (OSError, UnicodeError, ValueError):
            return ""
        if path.suffix.lower() == ".ipynb":
            text = project_files.notebook_view(text)
        if len(text) > 6000:
            text = text[:6000] + "\n..."
        return text

    def _finish_system(self, relative):
        return (
            "You are the builder. This file already exists. Read it and update it. "
            "Fill lines that are empty or only a placeholder, such as pass, ..., TODO, or NotImplementedError. "
            "Fix code that does not run. Keep lines that already work. "
            "%s "
            "If the check lists the names that exist, use those names. "
            "If a test expected a name that is not in that list, change the test. "
            "Leave a setup step that points at another computer, and finish the code after it. "
            "Search for the unfinished lines and replace them in this file's language:\n"
            "PATCH: %s\n"
            "<<<<<<< SEARCH\n"
            "the unfinished lines\n"
            "=======\n"
            "the finished lines in this file's language\n"
            ">>>>>>> REPLACE\n"
            "Use FILE only when the whole file must be rewritten, and write that same language. "
            "Do not create a Solved folder. Paths stay inside the project."
            % (project_files.language_for(relative), relative)
        )

    def _write_planned(self, plan, problem, route, root, project_name, council):
        """Each builder agrees on a file, then one writer builds or finishes that file."""
        notes = []
        total = len(plan)
        for index, rel in enumerate(plan, 1):
            if self._stop:
                break
            if project_files.is_answer_key(rel):
                self.publish({
                    "kind": "note",
                    "text": rel + " is a solved copy, so it stays unchanged.",
                })
                continue
            excerpt = ""
            finishing = False
            path = None
            if not project_files.needs_file(root, rel):
                try:
                    path = project_files.safe_path(root, rel)
                except ValueError:
                    path = None
                finishing = bool(path) and bool(project_files.source_gaps(path))
                if not finishing:
                    self.publish({"kind": "note", "text": rel + " is already in the project."})
                    continue
                excerpt = self._file_excerpt(root, rel)
            if finishing and path is not None and path.suffix.lower() == ".ipynb":
                self._finish_notebook_cells(rel, path, problem, route, root, project_name)
                notes.append(rel)
                continue
            shape = self._agree_file(council, rel, problem, route, root, excerpt)
            if self._stop:
                break
            if finishing:
                self.publish({"kind": "round", "label": "Finishing %s (%s of %s)" % (rel, index, total)})
                gaps = ""
                try:
                    gaps = "\n".join(project_files.source_gaps(project_files.safe_path(root, rel)))
                except ValueError:
                    gaps = ""
                text, summary, _edit = self._build(
                    self._finish_system(rel),
                    "Problem:\n%s\n\nAgreed route:\n%s\n\nThe builders agreed this file should:\n%s\n\n"
                    "What is unfinished:\n%s\n\nCurrent file:\n%s\n\n"
                    "Update %s so the missing code is finished and the broken code runs. "
                    "Keep the cells that already work."
                    % (
                        problem,
                        route,
                        shape or "Follow the agreed route.",
                        gaps or "(read the file and finish anything the task still needs)",
                        excerpt or "(empty)",
                        rel,
                    ),
                    4000,
                    True,
                    project_name,
                    continued=True,
                    think=True,
                )
            else:
                self.publish({"kind": "round", "label": "Writing %s (%s of %s)" % (rel, index, total)})
                text, summary, _edit = self._build(
                    self._one_file_system(rel),
                    "Problem:\n%s\n\nAgreed route:\n%s\n\nThe builders agreed this file should:\n%s\n\n"
                    "Files already here:\n%s\n\n"
                    "Write the complete file %s and no other file.\n\n%s"
                    % (
                        problem,
                        route,
                        shape or "Follow the agreed route.",
                        project_files.list_project(root) or "(empty)",
                        rel,
                        self._context(root, "", problem)[:6000],
                    ),
                    4000,
                    True,
                    project_name,
                )
            notes.append(summary or (text or "")[:400])
            if project_files.needs_file(root, rel):
                self.publish({
                    "kind": "note",
                    "text": rel + " did not come out as a complete file. It stays on the list.",
                })
            elif finishing:
                try:
                    left = project_files.source_gaps(project_files.safe_path(root, rel))
                except ValueError:
                    left = []
                if left:
                    self.publish({
                        "kind": "note",
                        "text": rel + " still needs code: " + "; ".join(left),
                    })
        return "\n".join(note for note in notes if note)

    def _one_file_system(self, relative):
        return (
            "You are the builder. Write one complete file and nothing else. Use this shape exactly:\n"
            "FILE: %s\n"
            "```\n"
            "the full file\n"
            "```\n"
            "%s "
            "Do not write any other file. Finish the file. Paths stay inside the project."
            % (relative, project_files.language_for(relative))
        )

    def _check_project(self, root):
        """Run the checks, and install a missing package into the project if that is why they failed."""
        plan = list(getattr(self, "_plan", None) or [])
        plan, dropped = project_files.drop_missing_answers(plan, root)
        if dropped:
            self._plan = plan
            self.publish({
                "kind": "note",
                "text": "No solved copy in this project. Continuing with the unfinished notebook.",
            })
        missing = project_files.missing_files(root, plan)
        if missing:
            return False, "These planned files are still missing or empty:\n" + "\n".join(missing)
        packages = project_files.requirements_to_install(root)
        if packages and not self._stop:
            if self._allow_install(packages, "The project lists these packages."):
                self.publish({"kind": "round", "label": "Installing project packages"})
                ok, log = project_files.install_packages(root, packages, stamp=True)
                self.publish({"kind": "note", "text": log})
        if self._stop:
            return False, "Stopped before the checks ran."
        self.publish({"kind": "round", "label": "Running tests"})
        passed, output = self._checked(root, *project_files.run_checks(root, getattr(self, "_problem", "")))
        if passed or self._stop:
            return passed, output
        for item in packages:
            base = project_files._pip_base(item).lower()
            if base:
                self._install_set().add(base)
                self._install_set().add("install:" + base)
        if not project_files.classify_failure(output, root, self._install_set()).get("action"):
            return passed, output
        ok, log = self._offer_install(root, output)
        if self._stop:
            return False, output
        if ok:
            self.publish({"kind": "round", "label": "Running tests"})
            return self._checked(root, *project_files.run_checks(root, getattr(self, "_problem", "")))
        if log:
            return False, (output + "\n\n" + log).strip()
        return False, output

    def _checked(self, root, passed, output):
        """A notebook that runs but still has empty or placeholder cells has not passed."""
        gaps = project_files.unfinished_report(root, getattr(self, "_plan", None))
        if gaps:
            return False, ((output or "") + "\n\n" + gaps).strip()
        return passed, output

    def _report_issues(self, problem, route, root, check_output):
        """Testers name what is wrong. They do not choose the next route."""
        self.publish({"kind": "round", "label": "The testers are naming the issues"})
        context = self._context(root, check_output, problem)
        issues = []
        heard = []
        for agent_id, name, stance in TESTERS:
            if self._stop:
                break
            speech = self._speak(
                agent_id,
                name,
                "You are %s, a tester. A check failed. %s "
                "Name what is wrong. Do not choose a new route and do not write code. "
                "Make it different from the testers who already spoke. Use this shape:\n"
                "ISSUE: what is wrong, in one sentence\n"
                "SAY: what you saw"
                % (name, stance),
                "Problem:\n%s\n\nCurrent route:\n%s\n\nCode:\n%s\n\nTest output:\n%s\n\n"
                "Issues already named:\n%s"
                % (
                    problem,
                    route,
                    context[:6000],
                    check_output[:2000],
                    "\n".join(heard) or "(none yet)",
                ),
                180,
                temperature=0.4,
                continued=True,
            )
            text = field(speech, "ISSUE") or (speech or "").strip()
            say = field(speech, "SAY")
            if say and say not in text:
                text = (text + " " + say).strip()
            text = text[:600] or "%s could not name an issue." % name
            issues.append({"id": agent_id, "name": name, "text": text})
            heard.append("%s: %s" % (name, text))
        if self._stop or not issues:
            return issues
        decision = self._speak(
            "tester-chair",
            "Chair",
            "You chair the testers. Agree on the issues to hand to the council. "
            "Do not choose a new route and do not write code. Use this shape:\n"
            "ISSUE: the shared problem in one sentence\nSAY: one sentence",
            "Problem:\n%s\n\nCurrent route:\n%s\n\nTesters:\n%s\n\nTest output:\n%s"
            % (problem, route, "\n".join(heard), (check_output or "")[:1500]),
            180,
            temperature=0.2,
            continued=True,
        )
        agreed = field(decision, "ISSUE") or (decision or "").strip()[:400]
        if agreed:
            issues.append({"id": "tester-chair", "name": "Chair", "text": agreed})
        return issues

    def _route_from_issues(self, council, problem, route, issues, check_output, yolo, root):
        """The council hears the issues and agrees one new route."""
        self.publish({"kind": "round", "label": "The council is working out a new route"})
        issue_text = "\n".join("%s: %s" % (item["name"], item["text"]) for item in issues) or "(none)"
        cause = project_files.explain_failure(check_output, root, self._install_set())
        heard = []
        by_id = {}
        agreed = False
        new_route = ""
        for round_n in range(1, 3):
            if self._stop:
                return ""
            for agent_id, name, stance in council:
                if self._stop:
                    return ""
                speech = self._speak(
                    agent_id,
                    name,
                    "You are %s on the council. The testers brought issues from the build. %s "
                    "Work out a new route that answers those issues. If another route already "
                    "does that, adopt it. Do not write code. "
                    "The Cause is the diagnosis. Follow it. If it says this is a code failure, "
                    "do not propose an install or a shorter folder. If it says an install already ran, "
                    "do not propose that install again. "
                    "Do not make the route a new Solved folder. The unfinished file already in the project is the work. "
                    "Use this shape:\n"
                    "ROUTE: one sentence\nSAY: two sentences"
                    % (name, stance),
                    "Cause:\n%s\n\nProblem:\n%s\n\nCurrent route:\n%s\n\nIssues:\n%s\n\nTest output:\n%s\n\n"
                    "Council so far:\n%s"
                    % (
                        cause,
                        problem,
                        route,
                        issue_text,
                        (check_output or "")[:1500],
                        "\n".join(heard) or "(nothing yet)",
                    ),
                    220,
                    temperature=0.5,
                    continued=round_n > 1,
                )
                proposed = field(speech, "ROUTE") or (speech or "").strip()[:400]
                by_id[agent_id] = {"id": agent_id, "name": name, "text": proposed}
                heard.append("%s: %s" % (name, proposed))
            if self._stop:
                return ""
            decision = self._speak(
                "chair",
                "Chair",
                "You chair the council. They are agreeing a new route for the testers' issues. "
                "The Cause is the diagnosis. If it says this is a code failure, the route has to change the code. "
                "Do not agree to an install or a shorter folder that the Cause does not ask for. "
                "Say they agree only when one route is clearly shared. Use this shape:\n"
                "AGREED: yes or no\nROUTE: one sentence\nASK: one sentence",
                "Cause:\n%s\n\nProblem:\n%s\n\nIssues:\n%s\n\nCouncil:\n%s"
                % (cause, problem, issue_text, "\n".join(heard)),
                200,
                temperature=0.2,
                continued=round_n > 1,
            )
            agreed, new_route, _note = parse_decision(decision)
            if agreed and new_route:
                break
        proposals = [by_id[agent_id] for agent_id, _name, _stance in council if agent_id in by_id]
        if not proposals and not new_route:
            return ""
        if yolo:
            return new_route or (proposals[0]["text"] if proposals else "")
        choice = self._ask_options(
            "The council each proposed a route for the testers' issues. Pick one, or write the route you want.",
            issue_text,
            [{"name": item["name"], "text": item["text"]} for item in proposals],
        )
        if self._stop or (choice or "").strip().lower() in ("stop", "quit", "cancel"):
            return "stop"
        return choose_route(proposals, choice) or new_route

    def _ask(self, question, detail):
        self.publish({
            "kind": "ask",
            "question": question,
            "detail": detail,
        })
        try:
            return self._answers.get(timeout=6 * 60 * 60)
        except queue.Empty:
            return "stop"

    def _builder_system(self, project_mode, revise=False):
        if project_mode:
            if revise:
                return (
                    "You are the builder. The route is already agreed. "
                    "Start your answer with PATCH. Do not explain first. "
                    "Write each file in the language its extension uses. "
                    "A .py file and a notebook code cell are Python, with None, True, and False. "
                    "A .js or .ts file is JavaScript or TypeScript. A .sql file is SQL. A .R file is R. "
                    "Do not put notebook JSON in a cell, and do not put one language in another file type. "
                    "Do not create a Solved folder or a solution notebook. Finish the unfinished file that is already in the project. "
                    "Read the check first. If it lists the names that exist, use those names. "
                    "If a test expected a name that is not in that list, change the test instead of inventing the name. "
                    "If a setup step points at another computer, leave that setup and patch the unfinished code after it. "
                    "A blank cell at the end of a notebook is not the missing work. "
                    "A cell with no finished code has to be filled, then run. "
                    "A section title, such as a markdown heading in a code cell, is not a cell that needs code. "
                    "Read the traceback first and change that cell. "
                    "A NameError means the name was assigned in an earlier cell or needs the import that already defines it. "
                    "If a cell asks for a data file by name and that file is missing, keep the name and bring that file in. "
                    "Do not point the read at a different dataset. "
                    "The run output says whether that code is right. "
                    "Do not stop at naming the unread file or the empty cell. "
                    "Do not write a placeholder file to make the check pass. "
                    "This is the same for a csv, a json file, a spreadsheet, or any other data file. "
                    "This same patch shape works for a notebook cell, a Python file, or any other source file. "
                    "Change only the lines that are unfinished or wrong. "
                    "Copy the old lines exactly, without the line numbers, in this shape:\n"
                    "PATCH: path/file.py\n"
                    "<<<<<<< SEARCH\n"
                    "exact lines from the file\n"
                    "=======\n"
                    "replacement lines\n"
                    ">>>>>>> REPLACE\n"
                    "Example:\n"
                    "PATCH: app.py\n"
                    "<<<<<<< SEARCH\n"
                    "return a - b\n"
                    "=======\n"
                    "return a + b\n"
                    ">>>>>>> REPLACE\n"
                    "A notebook is shown as cells, not as JSON. "
                    "The line CELL 2 code: is only a label in that view. Never write that line into a file. "
                    "Use a data file's column names exactly as they are spelled. "
                    "PATCH the cell code itself. Do not patch the JSON, and do not copy the CELL heading:\n"
                    "PATCH: notes.ipynb\n"
                    "<<<<<<< SEARCH\n"
                    "print(x + \"a\")\n"
                    "=======\n"
                    "print(x)\n"
                    ">>>>>>> REPLACE\n"
                    "Leave ! and % lines out of new notebook code. "
                    "If a cell is empty or only a placeholder, fill that cell. "
                    "Use the CELL heading as the search text when the cell has no code:\n"
                    "PATCH: notes.ipynb\n"
                    "<<<<<<< SEARCH\n"
                    "CELL 2 code:\n"
                    "=======\n"
                    "finished code for that cell\n"
                    ">>>>>>> REPLACE\n"
                    "The same PATCH shape works for any source file. Use the file's real path, "
                    "such as App.kt, query.sql, or Makefile. "
                    "Use several PATCH blocks when more than one spot is wrong. "
                    "Use FILE to rewrite a notebook when several cells must be finished, and include the cells that already work. "
                    "That FILE is the Python for each cell, with a line that is exactly # %% between cells. Do not write notebook JSON. "
                    "Use FILE to add a file that does not exist yet:\n"
                    "FILE: path/new.py\n"
                    "```python\n"
                    "code\n"
                    "```\n"
                    "Leave every other file untouched. Paths stay inside the project."
                )
            if any(str(item).lower().endswith(".ipynb") for item in (getattr(self, "_plan", None) or [])):
                return (
                    "You are the builder. Complete the notebook. Do not reopen the debate. "
                    "Do not write test_app.py or main.py. Running the notebook is the check. "
                    "Write real files. Use this shape for every folder and file:\n"
                    "DIR: folder/name\n"
                    "FILE: folder/name/notes.ipynb\n"
                    "```\n"
                    "cell code\n"
                    "# %%\n"
                    "next cell\n"
                    "```\n"
                    "For a Jupyter notebook, write the cell code and separate cells with a line that is exactly # %%. "
                    "Do not write notebook JSON, and do not include ! or % lines. "
                    "Paths stay inside the project."
                )
            return (
                "You are the builder. Create the project. Do not reopen the debate. "
                "Write real files. Use this shape for every folder and file:\n"
                "DIR: folder/name\n"
                "FILE: folder/name/file.py\n"
                "```python\n"
                "code\n"
                "```\n"
                "Put a runnable main.py at the project root when the task is a program. "
                "Also write test_app.py with unittest tests that check the real behavior, "
                "and end that file with unittest.main(). "
                "main.py and test_app.py will be run. If they fail, you will patch the failing lines. "
                "If the program needs a third-party package, add requirements.txt with one package name per line. "
                "Match each file's extension. A .py file is Python. A notebook's code cells are Python, not JSON. "
                "A .sql file is SQL. A .js file is JavaScript. A .R file is R. "
                "For a Jupyter notebook, write the cell code and separate cells with a line that is exactly # %%. "
                "Do not write notebook JSON, and do not include ! or % lines. "
                "Paths stay inside the project."
            )
        if revise:
            return (
                "Revise the work once, using the council's objections. Keep the agreed route. "
                "When code helps, end with one ```python block."
            )
        return (
            "You carry out the route the council agreed. Do not reopen the debate. "
            "When code helps, end with one ```python block that can be run as a script."
        )

    def _direct_patch(self, problem, route, root, project_name, check_output):
        """One edit from the check itself, before another debate."""
        info = project_files.classify_failure(check_output, root, self._install_set())
        if info["kind"] in {"permission", "no_build", "long_path"} and not info.get("action"):
            self.publish({"kind": "note", "text": info["cause"]})
            return
        self.publish({"kind": "round", "label": "Fixing what the check showed"})
        focus = project_files.focus_failure(check_output)
        excerpt = project_files.read_for_fix(root, check_output, problem)[:4500]
        obey = (
            "The user injected a fix. Apply that fix even when the cause suggests something else. "
            if (getattr(self, "_user_fix", "") or "").strip()
            else "Do not explain first, and do not discuss the route. "
        )
        system = (
            "You fix the failure in front of you. Read the cause first and patch only what that cause needs. "
            "If the cause names a missing file, keep that file name and do not swap in a different dataset. "
            "If the cause names an undefined variable, use a name assigned in an earlier cell or the import that defines it. "
            "A section-title cell is not work. "
            "If cells have no finished code, fill those cells so they can run. "
            "Do not stop after naming the unread file or the empty cell. "
            "Start the answer with PATCH. "
            + obey
            + "Copy the old lines exactly, without line numbers.\n"
            "PATCH: path/file.py\n"
            "<<<<<<< SEARCH\n"
            "exact lines\n"
            "=======\n"
            "replacement lines\n"
            ">>>>>>> REPLACE\n"
            "Read the file path in the check and write that file's language. "
            "A notebook code cell is Python, not JSON. A .py file is Python. "
            "A .sql file is SQL. A .js file is JavaScript. A .R file is R. "
            "Never write a line that starts with CELL. "
            "Use the names the check printed. Leave a setup step that points at another computer. "
            "If a method is listed for this kind of failure, apply that method."
        )
        user = (
            "%sCause:\n%s\n\nProblem:\n%s\n\nAgreed route:\n%s\n\nCheck:\n%s\n\nCurrent code:\n%s"
            % (
                self._fix_prefix(),
                info["cause"],
                (problem or "")[:1200],
                (route or "")[:800],
                focus,
                excerpt,
            )
        )
        before = project_files.snapshot_project(root)
        self._build(system, user, 1600, True, project_name, continued=True, think=False)
        if self._stop or project_files.snapshot_project(root) != before:
            return
        self.publish({"kind": "note", "text": "That reply did not change the file."})
        self._build(
            "Output only PATCH blocks. Do not explain. Copy the old lines exactly.",
            "%sCheck:\n%s\n\nCurrent code:\n%s" % (self._fix_prefix(), focus, excerpt[:2500]),
            900,
            True,
            project_name,
            continued=True,
            think=False,
        )

    def _build(self, system, user, limit, project_mode, project_name, continued=False, think=False):
        agent_id, name = getattr(self, "_writer", None) or ("builder-small", "Wren")
        text = self._speak(
            agent_id,
            name,
            system,
            user,
            limit,
            temperature=0.3,
            continued=continued,
            think=think,
        )
        if not project_mode:
            code = extract_python(text)
            output = ""
            if code:
                filename, output = run_python(code, "agreed-route")
                self.publish({
                    "kind": "file",
                    "id": agent_id,
                    "name": name,
                    "path": filename,
                    "code": code,
                })
                if output:
                    self.publish({
                        "kind": "output",
                        "id": agent_id,
                        "name": name,
                        "text": output,
                    })
            return text, output, output
        _root, summary, events, _output = project_files.apply_manifest(
            project_name, text, revise=continued
        )
        if not any(event.get("kind") == "file" for event in events):
            events.extend(project_files.salvage_edits(_root, text))
        notes = []
        for event in events:
            if event.get("kind") == "note":
                notes.append(event.get("text") or "")
            published = dict(event)
            published["id"] = agent_id
            published["name"] = name
            self.publish(published)
        return text, summary, "\n".join(note for note in notes if note)

    def _speak(self, agent_id, name, system, user, limit, temperature=0.4, continued=False, think=False):
        if self._stop:
            return ""
        self.publish({"kind": "agent", "id": agent_id, "name": name, "state": "working"})
        if self._lesson_block:
            user = self._lesson_block + "\n\n" + user
        chunks = []
        if continued:
            self.publish({"kind": "token", "id": agent_id, "name": name, "text": "\n\n"})

        def on_delta(piece):
            chunks.append(piece)
            self.publish({"kind": "token", "id": agent_id, "name": name, "text": piece})

        def on_think(piece):
            self.publish({"kind": "token", "id": agent_id, "name": name, "text": piece})

        last_error = None
        for attempt in range(2):
            try:
                stream_chat(
                    system, user, on_delta, limit, temperature,
                    think=think, on_think=on_think if think else None,
                )
                last_error = None
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RuntimeError(
                        "Ollama answered, but %s is not in its model folder. "
                        "The tray app started with a different folder than this project." % MODEL
                    ) from exc
                last_error = RuntimeError("The local model rejected the request. %s" % exc)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError, OSError) as exc:
                last_error = RuntimeError("The local model stopped answering. %s" % exc)
            except RuntimeError as exc:
                last_error = exc
            if chunks or attempt == 1 or _model_gone(last_error):
                break
            message = str(last_error).lower()
            if "context" in message or "num_ctx" in message or "exceed" in message:
                user = _fit_prompt(user, 8000)
            self.publish({"kind": "note", "text": "The model stopped answering. Trying that turn once more."})
        if last_error is not None and not chunks:
            if _model_gone(last_error):
                self._stop = True
                self.publish({
                    "kind": "note",
                    "text": "The local model is not answering, so this run has stopped.",
                })
            else:
                self.publish({
                    "kind": "note",
                    "text": "The model could not finish that turn. The run is still going. " + str(last_error),
                })
            self.publish({"kind": "agent", "id": agent_id, "name": name, "state": "done"})
            return ""
        text = "".join(chunks).strip()
        self.publish({"kind": "agent", "id": agent_id, "name": name, "state": "done"})
        return text

    def _context(self, root, check_output, problem):
        text = project_files.read_for_fix(root, check_output, problem)
        block = memory.code_block(problem, project=str(root))
        if not block:
            return text
        return block + "\n\n" + text

    def _keep_what_worked(self, root, problem, approach, before, project_name):
        self._store_learning(problem, approach, project_name, before, project_files.snapshot_project(root))

    def _store_learning(self, problem, approach, project_name, before, after):
        changes = project_files.changed_files(before, after)
        files = [item["path"] for item in changes]
        try:
            memory.remember_code(str(project_name), problem, files, approach)
            if changes:
                memory.remember_edit(problem, str(project_name), changes)
        except OSError:
            return
        if files:
            self.publish({"kind": "note", "text": "Saved what worked in " + ", ".join(files) + " for a later run."})
        else:
            self.publish({"kind": "note", "text": "Saved a note about the code that passed."})


def _fit_prompt(user, limit=32000):
    """Keep a prompt inside the context window so a long check cannot end the run."""
    text = user or ""
    if len(text) <= limit:
        return text
    tail = max(1000, limit - 4500)
    return (
        text[:4000]
        + "\n\nThe middle of this prompt was left out so the model can finish.\n\n"
        + text[-tail:]
    )


_streams = []
_streams_lock = threading.Lock()


def cancel_streams():
    """Close a model call that is still waiting so Stop can unlock the page."""
    with _streams_lock:
        streams = list(_streams)
    for response in streams:
        try:
            response.close()
        except Exception:
            pass


def _model_gone(exc):
    text = str(exc or "").lower()
    marks = (
        "10061",
        "10053",
        "10054",
        "actively refused",
        "connection refused",
        "failed to establish",
        "timed out",
        "timeout",
        "not running",
        "connection reset",
        "aborted",
        "closed",
    )
    return any(mark in text for mark in marks)


def stream_chat(system, user, on_delta, limit, temperature, think=False, on_think=None):
    user = _fit_prompt(user)
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "think": bool(think),
        "options": {"temperature": temperature, "num_predict": limit, "num_ctx": memory.NUM_CTX},
    }).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        with _streams_lock:
            _streams.append(response)
        try:
            for raw in response:
                line = raw.strip()
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                if data.get("error"):
                    raise RuntimeError(data["error"])
                message = data.get("message") or {}
                thought = message.get("thinking") or ""
                piece = message.get("content") or ""
                if thought and on_think:
                    on_think(thought)
                if piece:
                    on_delta(piece)
                if data.get("done"):
                    break
        finally:
            with _streams_lock:
                if response in _streams:
                    _streams.remove(response)


def _text_tail(text, limit):
    """The end of a message. A short message stays whole."""
    text = text if isinstance(text, str) else str(text or "")
    return text[-limit:]


def _last_line(text):
    lines = _text_tail(text, 4000).splitlines()
    return (lines[-1] if lines else "")[:180]


def _cell_compiles(code):
    """True when the cell is Python. A runtime failure can still be written."""
    body = "\n".join(
        line for line in (code or "").splitlines()
        if not line.strip().startswith(("!", "%"))
    )
    if not body.strip() or project_files.is_notebook_dump(body):
        return False
    try:
        compile(body, "<cell>", "exec")
    except SyntaxError:
        return False
    return True


def clean_name(value, fallback):
    text = " ".join((value or "").split())
    text = "".join(ch for ch in text if ch.isalnum() or ch in " .'-")
    text = text.strip()[:32]
    return text or fallback


def unique_names(names):
    seen = {}
    unique = []
    for name in names:
        count = seen.get(name.casefold(), 0) + 1
        seen[name.casefold()] = count
        unique.append(name if count == 1 else "%s %s" % (name, count))
    return unique


def collective_text(team_name, route, answer, run_output):
    parts = [team_name, ""]
    if route:
        parts.extend(["Agreed route: %s" % route, ""])
    if answer:
        parts.append(answer.strip())
    if run_output:
        parts.extend(["", "Run output:", run_output.strip()])
    return "\n".join(parts).strip()


def hands_off(problem, route):
    """True when the latest user message asked to study, not to write or run the project.

    Earlier chats and a council route are not the request. A line in those chats that says
    "without executing" must not stop a request to finish a file.
    """
    if work_requested(problem):
        return False
    text = project_files.latest_request(problem).lower()
    marks = (
        "do not write",
        "don't write",
        "do not plan a build",
        "without executing",
        "do not execute",
        "don't execute",
        "do not change anything",
        "do not change any",
    )
    return any(mark in text for mark in marks)


def work_requested(problem):
    """True when the latest message asks for a file to be finished."""
    text = project_files.latest_request(problem).lower()
    if re.search(r"\b(do not|don't|dont)\s+(write|change|modify|execute|plan)\b", text):
        if not re.search(r"\b(complete|fill|finish|solve|implement)\b", text):
            return False
    return bool(re.search(r"\b(complete|fill|finish|solve|implement|fix)\b", text))


def study_route(route):
    """True when a route tells the team to look and not edit."""
    text = (route or "").lower()
    marks = (
        "static analysis",
        "without executing",
        "do not execute",
        "don't execute",
        "without modifying",
        "leave the files",
        "left the files",
    )
    return any(mark in text for mark in marks)


def injected_fix(text):
    """A written direction. A yes, no, or a click on someone else's route is not one."""
    raw = (text or "").strip()
    if not raw or answer_kind(raw) in ("yes", "no", "stop", "continue"):
        return ""
    if raw.lower().startswith("use "):
        head = raw[4:].split(":", 1)[0].strip()
        if head and " " not in head and len(head) <= 32:
            return ""
    return raw


def blocks_fix(lesson):
    """Lessons that tell the team to study instead of edit."""
    text = (lesson or "").lower()
    marks = (
        "static analysis",
        "left the files alone",
        "without modifying",
        "do not modify",
        "without executing",
    )
    return any(mark in text for mark in marks)


def answer_kind(text):
    """Tell a yes, no, continue, stop, or free-text answer apart."""
    low = (text or "").strip().lower()
    if low.startswith("use "):
        head = low[4:].split(":", 1)[0].strip()
        if head in ("yes", "y"):
            return "yes"
        if head in ("no", "n"):
            return "no"
        if head.startswith("keep"):
            return "continue"
        if head == "stop":
            return "stop"
    if low in ("stop", "quit", "cancel"):
        return "stop"
    if low in ("don't", "dont", "do not", "skip", "don't install", "dont install", "do not install"):
        return "no"
    if re.fullmatch(r"(do not|don't|dont)\s+install( them| it| this)?", low):
        return "no"
    if len(low) <= 80 and re.search(r"\b(yes|install)\b", low) and not re.search(r"\bno\b", low):
        return "yes"
    if low in ("no", "n") or low.startswith("no ") or low.startswith("no,"):
        return "no"
    if len(low) <= 80 and re.search(r"\b(continue|keep going)\b", low):
        return "continue"
    return "text"


def failure_key(output):
    """A short signature for a repeated check failure."""
    lines = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if "error" in lowered or lowered.startswith(("fail", "e ", "traceback", "assert")):
            lines.append(line[:180])
    text = "\n".join(lines[-6:]) or (output or "")[-400:]
    return re.sub(r"\s+", " ", text).strip().lower()[:500]


def match_named(proposals, guidance):
    """The proposal whose name the answer picked."""
    text = (guidance or "").strip()
    lead = re.match(r"(?i)^use\s+([^:]+)", text)
    if lead:
        who = lead.group(1).strip().lower()
        for item in proposals or []:
            if (item.get("name") or "").lower() == who:
                return item
    exact = text.lower()
    for item in proposals or []:
        if (item.get("name") or "").lower() == exact:
            return item
    for item in proposals or []:
        name = item.get("name") or ""
        if name and re.search(r"\b%s\b" % re.escape(name), text, re.I):
            return item
    return None


def choose_route(proposals, guidance):
    """Keep the route the user named, or the route they wrote themselves."""
    text = (guidance or "").strip()
    named = match_named(proposals, text)
    fix = injected_fix(text)
    if named and text.lower().startswith("use ") and not fix:
        return named.get("text") or ""
    if fix:
        return fix
    if text and not text.lower().startswith("use "):
        return text
    if named:
        return named.get("text") or ""
    if text:
        return text
    if proposals:
        return proposals[0].get("text") or ""
    return ""


def _plan_rule(problem, root):
    """How builders should name files. A notebook completion is not a test_app.py project."""
    if project_files.notebook_task(problem, root):
        return (
            "The user asked for a Jupyter notebook to be completed. "
            "List that notebook and the data files it needs. "
            "Do not add test_app.py or main.py. Running the notebook is the check. "
            "Use at most 8 files. "
        )
    return (
        "Use at most 8 files. Include main.py or one .ipynb when the work is Python, "
        "and test_app.py for a Python project. For any other language, list the real source files "
        "and do not add a Python file just to have one. "
    )


def _chair_plan_rule(problem, root):
    if project_files.notebook_task(problem, root):
        return (
            "Keep the notebook. Do not add test_app.py or main.py. "
            "Running the notebook is the check. "
        )
    return "Prefer the smallest list that still has a way to run and a test. "


def choose_plan(proposals, guidance):
    """Keep the file list the user named, or the paths they wrote themselves."""
    text = guidance or ""
    named = None
    for item in proposals or []:
        name = item.get("name") or ""
        if name and re.search(r"\b%s\b" % re.escape(name), text, re.I) and item.get("plan"):
            named = item["plan"]
            break
    parsed = project_files.parse_plan(text)
    if not parsed:
        tokens = [
            token.strip().strip("`").strip(".").replace("\\", "/")
            for token in re.split(r"[\s,]+", text)
            if token.strip()
        ]
        parsed = project_files.parse_plan("PLAN:\n" + "\n".join(tokens))
    if parsed and not text.lower().lstrip().startswith("use "):
        return parsed
    if named:
        return named
    if parsed:
        return parsed
    if proposals and proposals[0].get("plan"):
        return proposals[0]["plan"]
    return ["main.py", "test_app.py"]


def field(text, label):
    match = re.search(r"(?im)^%s:\s*(.+)$" % re.escape(label), text or "")
    return match.group(1).strip() if match else ""


def parse_decision(text):
    agreed = field(text, "AGREED").lower().startswith("y")
    route = field(text, "ROUTE")
    note = field(text, "ASK") or field(text, "WHY")
    if not note:
        note = (text or "").strip()[:400]
    return agreed, route, note


def last_route(transcript):
    route = ""
    for _, _, text in transcript:
        found = field(text, "ROUTE")
        if found:
            route = found
    return route


def render_transcript(items):
    lines = []
    for round_n, name, text in items:
        lines.append("Round %s, %s:\n%s" % (round_n, name, (text or "").strip()))
    return "\n\n".join(lines)[-6000:]


def extract_python(text):
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S | re.I)
    if not blocks:
        return None
    return blocks[-1].strip() or None


def run_python(code, label):
    WORKSPACE.mkdir(exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-").lower() or "approach"
    path = WORKSPACE / ("%s.py" % slug[:60])
    path.write_text(code, encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        output = output.strip() or "(no output, exit %s)" % completed.returncode
    except subprocess.TimeoutExpired:
        output = "Stopped: the script ran longer than 15 seconds."
    return path.name, output


swarm = Swarm()
