import json
import re
import unittest

from pwexam import attempts, clock, exams, notify, questions, results, users
from pwexam.config import settings
from pwexam.db import Database
from pwexam.errors import AuthError, Conflict, Forbidden, ValidationError

settings.secret = "test-secret"


class Base(unittest.TestCase):
    def setUp(self):
        clock.reset()
        settings.otp_candidates = False
        self.db = Database(":memory:")
        with self.db.tx() as c:
            users.ensure_admin(c, "admin", "Admin@1234")
            self.admin = dict(c.execute("SELECT * FROM users WHERE login_id='admin'").fetchone())
            self.batch = users.save_batch(c, self.admin, {"name": "Batch A"})
            self.other_batch = users.save_batch(c, self.admin, {"name": "Batch B"})
            self.cands = [users.create_candidate(c, self.admin, {
                "batch_id": self.batch["id"], "login_id": f"C{i}", "name": f"Cand {i}", "email": f"c{i}@x.in",
                "password": "Pass1234"}) for i in range(1, 4)]
            self.q = [questions.save_question(c, self.admin, {
                "subject": "Maths" if i < 3 else "Physics", "text": f"Q{i}", "options": ["a", "b", "c", "d"],
                "correct": [1], "marks": 4, "negative": 1, "difficulty": "easy" if i % 2 else "hard"})
                for i in range(5)]
            self.q.append(questions.save_question(c, self.admin, {
                "subject": "Physics", "text": "Multi", "qtype": "multi", "options": ["w", "x", "y", "z"],
                "correct": [0, 2], "marks": 4, "negative": 2}))
            self.exam = exams.save_exam(c, self.admin, {
                "title": "Mock", "duration_min": 30, "question_ids": [q["id"] for q in self.q],
                "max_violations": 2, "grace_seconds": 60})
            now = clock.now()
            self.sched = exams.save_schedule(c, self.admin, {
                "exam_id": self.exam["id"], "batch_id": self.batch["id"], "starts_at": now - 60,
                "ends_at": now + 7200})

    def tearDown(self):
        clock.reset()

    def user(self, login):
        with self.db.read() as c:
            return dict(c.execute("SELECT * FROM users WHERE login_id=?", (login,)).fetchone())

    def tx(self, fn, *a, **kw):
        with self.db.tx() as c:
            return fn(c, *a, **kw)

    def start(self, login="C1"):
        return self.tx(attempts.start_attempt, self.user(login), self.sched["id"])

    def answer(self, attempt, login, qid, selected, seq=1, **kw):
        return self.tx(attempts.save_answers, self.user(login), attempt["id"],
                       [{"question_id": qid, "selected": selected, "seq": seq, **kw}])


class AuthTest(Base):
    def test_login_and_token(self):
        res = self.tx(users.login, "c1", "Pass1234")
        with self.db.read() as c:
            u = users.authenticate(c, res["token"])
        self.assertEqual(u["login_id"], "C1")
        self.assertNotIn("password_hash", res["user"])

    def test_lockout_after_failures(self):
        for _ in range(settings.max_failed_logins):
            self.assertIn("_auth_error", self.tx(users.login, "C1", "wrong"))
        with self.assertRaisesRegex(AuthError, "Too many"):
            self.tx(users.login, "C1", "Pass1234")
        clock.advance(settings.lockout_seconds + 1)
        self.assertIn("token", self.tx(users.login, "C1", "Pass1234"))

    def test_logout_invalidates_tokens(self):
        tok = self.tx(users.login, "C1", "Pass1234")["token"]
        self.tx(users.logout_everywhere, self.user("C1"))
        with self.db.read() as c, self.assertRaises(AuthError):
            users.authenticate(c, tok)

    def test_tampered_or_expired_token(self):
        tok = self.tx(users.login, "C1", "Pass1234")["token"]
        with self.db.read() as c:
            with self.assertRaises(AuthError):
                users.authenticate(c, tok[:-2] + "xx")
            clock.advance(settings.candidate_token_hours * 3600 + 5)
            with self.assertRaisesRegex(AuthError, "expired"):
                users.authenticate(c, tok)

    def test_otp_flow(self):
        settings.otp_candidates = True
        res = self.tx(users.login, "C1", "Pass1234")
        self.assertTrue(res["otp_required"])
        with self.db.read() as c:
            n = c.execute("SELECT body FROM notifications ORDER BY id DESC").fetchone()[0]
            self.assertTrue(all("OTP" not in x["body"] or "hidden" in x["body"] for x in notify.list_notifications(c)))
        code = re.search(r"\b(\d{6})\b", n).group(1)
        self.assertIn("_auth_error", self.tx(users.verify_otp, res["otp_token"], "000000" if code != "000000" else "111111"))
        self.assertIn("token", self.tx(users.verify_otp, res["otp_token"], code))
        with self.assertRaises(AuthError):  # single use
            self.tx(users.verify_otp, res["otp_token"], code)

    def test_permissions(self):
        with self.db.tx() as c:
            ex = users.create_staff(c, self.admin, {"role": "examiner", "login_id": "ex", "name": "Ex",
                                                    "password": "Exam1234"})
        exr = self.user("ex")
        users.require(exr, "questions")
        for perm in ("users", "results", "audit", "take_exam"):
            with self.assertRaises(Forbidden):
                users.require(exr, perm)
        with self.assertRaises(Forbidden):
            users.require(self.user("C1"), "questions")
        self.assertTrue(ex["initial_password"])

    def test_candidate_import(self):
        out = self.tx(users.import_candidates, self.admin, self.batch["id"],
                      "login_id,name,email\nN1,New One,n1@x.in\nC1,Dup,\nN2,,\nN3,Three,bad-email")
        self.assertEqual([c["login_id"] for c in out["created"]], ["N1"])
        self.assertEqual(len(out["errors"]), 3)


class QuestionTest(Base):
    def test_validation(self):
        bad = [{"subject": "S", "text": "", "options": ["a", "b"], "correct": [0]},
               {"subject": "S", "text": "t", "options": ["a"], "correct": [0]},
               {"subject": "S", "text": "t", "options": ["a", "b"], "correct": [0, 1]},
               {"subject": "S", "text": "t", "options": ["a", "a"], "correct": [0]},
               {"subject": "S", "text": "t", "options": ["a", "b"], "correct": [5]}]
        for b in bad:
            with self.subTest(b=b), self.assertRaises(ValidationError):
                self.tx(questions.save_question, self.admin, b)

    def test_csv_import(self):
        csv_text = ("subject,topic,type,difficulty,question,option_a,option_b,option_c,option_d,correct,marks,negative\n"
                    "Bio,Cell,,easy,Powerhouse?,Nucleus,Mitochondria,Ribosome,,B,4,1\n"
                    "Bio,Cell,multi,medium,Pick two,A1,A2,A3,A4,\"A;C\",4,0\n"
                    "Bio,Cell,,easy,Broken,X,Y,,,E,4,1\n")
        out = self.tx(questions.import_csv, self.admin, csv_text)
        self.assertEqual(out["created"], 2)
        self.assertEqual(out["errors"][0]["line"], 4)

    def test_used_question_is_deactivated_not_deleted(self):
        self.assertIn("deactivated", self.tx(questions.delete_question, self.admin, self.q[0]["id"]))


class ExamFlowTest(Base):
    def test_start_hides_answers_and_shuffles_all(self):
        a = self.start()
        self.assertEqual(sorted(q["id"] for q in a["questions"]), sorted(q["id"] for q in self.q))
        blob = json.dumps(a)
        self.assertNotIn('"correct"', blob)
        self.assertNotIn("explanation", blob)
        self.assertEqual(a["remaining"], 30 * 60)
        # resume returns the same paper
        again = self.start()
        self.assertEqual([q["id"] for q in again["questions"]], [q["id"] for q in a["questions"]])

    def test_wrong_batch_and_window(self):
        with self.db.tx() as c:
            users.update_user(c, self.admin, self.cands[1]["id"], {"batch_id": self.other_batch["id"]})
        with self.assertRaises(Forbidden):
            self.start("C2")
        with self.db.tx() as c:
            s2 = exams.save_schedule(c, self.admin, {"exam_id": self.exam["id"], "batch_id": self.other_batch["id"],
                                                     "starts_at": clock.now() + 3600, "ends_at": clock.now() + 7200})
        with self.assertRaisesRegex(ValidationError, "not started"):
            self.tx(attempts.start_attempt, self.user("C2"), s2["id"])

    def test_answers_seq_and_validation(self):
        a = self.start()
        qid = self.q[0]["id"]
        self.answer(a, "C1", qid, [2], seq=5)
        self.answer(a, "C1", qid, [1], seq=3)  # older offline write must not win
        with self.db.read() as c:
            self.assertEqual(json.loads(c.execute("SELECT selected FROM answers WHERE question_id=?",
                                                  (qid,)).fetchone()[0]), [2])
        r = self.answer(a, "C1", qid, [0, 1], seq=9)  # two answers on single-choice
        self.assertEqual(r["rejected"], 1)
        r = self.answer(a, "C1", 99999, [0], seq=9)
        self.assertEqual(r["rejected"], 1)

    def test_offline_grace_period(self):
        a = self.start()
        deadline = a["deadline"]
        clock.advance(30 * 60 + 30)  # past deadline, inside 60s grace
        r = self.answer(a, "C1", self.q[0]["id"], [1], seq=1, client_ts=deadline - 10)
        self.assertEqual(r["accepted"], 1)
        r = self.answer(a, "C1", self.q[1]["id"], [1], seq=1, client_ts=deadline + 20)
        self.assertEqual(r["rejected"], 1)
        clock.advance(60)
        self.assertEqual(attempts.auto_submit_expired(self.db), 1)
        with self.assertRaises(Conflict):
            self.answer(a, "C1", self.q[2]["id"], [1], seq=2)
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM attempts").fetchone()[0], "auto_submitted")

    def test_violations_terminate(self):
        a = self.start()
        u = self.user("C1")
        r = self.tx(attempts.record_violation, u, a["id"], "tab_switch")
        self.assertEqual(r["violations"], 1)
        r = self.tx(attempts.record_violation, u, a["id"], "window_blur")  # same moment: not double counted
        self.assertEqual(r["violations"], 1)
        r = self.tx(attempts.record_violation, u, a["id"], "copy")  # logged only
        self.assertEqual(r["violations"], 1)
        clock.advance(5)
        self.tx(attempts.record_violation, u, a["id"], "fullscreen_exit")
        clock.advance(5)
        r = self.tx(attempts.record_violation, u, a["id"], "tab_switch")
        self.assertTrue(r["terminated"])
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM attempts").fetchone()[0], "terminated")

    def test_scoring_negative_and_multi(self):
        a = self.start()
        ids = [q["id"] for q in self.q]
        self.answer(a, "C1", ids[0], [1])      # +4
        self.answer(a, "C1", ids[1], [0])      # -1
        self.answer(a, "C1", ids[5], [0, 2])   # +4 (multi exact)
        out = self.tx(attempts.submit, self.user("C1"), a["id"],
                      [{"question_id": ids[2], "selected": [0], "seq": 1}])  # -1, sent with submit
        self.assertEqual(out["status"], "submitted")
        with self.db.read() as c:
            row = dict(c.execute("SELECT * FROM attempts").fetchone())
        self.assertEqual((row["score"], row["max_score"], row["correct"], row["wrong"], row["unanswered"]),
                         (6.0, 24.0, 2, 2, 2))
        with self.assertRaises(Conflict):
            self.start()

    def test_partial_multi_is_wrong(self):
        a = self.start()
        self.answer(a, "C1", self.q[5]["id"], [0])
        self.tx(attempts.submit, self.user("C1"), a["id"])
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT score FROM attempts").fetchone()[0], -2.0)

    def test_random_selection(self):
        with self.db.tx() as c:
            e = exams.save_exam(c, self.admin, {"title": "R", "duration_min": 10, "selection_mode": "random",
                                                "random_rules": [{"subject": "Physics", "count": 2},
                                                                 {"subject": "Maths", "count": 3}]})
            with self.assertRaisesRegex(ValidationError, "Only"):
                exams.save_exam(c, self.admin, {"title": "R2", "duration_min": 10, "selection_mode": "random",
                                                "random_rules": [{"subject": "Physics", "count": 9}]})
            s = exams.save_schedule(c, self.admin, {"exam_id": e["id"], "batch_id": self.batch["id"],
                                                    "starts_at": clock.now(), "ends_at": clock.now() + 3600})
        a = self.tx(attempts.start_attempt, self.user("C1"), s["id"])
        subj = sorted(q["subject"] for q in a["questions"])
        self.assertEqual(subj, ["Maths"] * 3 + ["Physics"] * 2)
        self.assertEqual(len({q["id"] for q in a["questions"]}), 5)

    def test_exam_locked_after_attempt(self):
        self.start()
        with self.assertRaisesRegex(ValidationError, "Duplicate"):
            self.tx(exams.save_exam, self.admin, dict(self.exam, title="changed"), self.exam["id"])
        copy = self.tx(exams.duplicate_exam, self.admin, self.exam["id"])
        self.assertEqual(copy["question_count"], len(self.q))


class ResultsTest(Base):
    def finish(self, login, correct_ids, wait=0):
        a = self.start(login)
        clock.advance(wait)
        items = [{"question_id": q, "selected": [1], "seq": 1} for q in correct_ids]
        self.tx(attempts.submit, self.user(login), a["id"], items)
        return a

    def test_rank_percentile_publish(self):
        ids = [q["id"] for q in self.q[:5]]
        a1 = self.finish("C1", ids[:5], wait=100)   # 20
        self.finish("C2", ids[:5], wait=50)          # 20, faster -> rank 1
        a3 = self.finish("C3", ids[:2])              # 8
        with self.db.read() as c:
            res = results.schedule_results(c, self.sched["id"])
        self.assertEqual([(r["login_id"], r["rank"]) for r in res["rows"]], [("C2", 1), ("C1", 2), ("C3", 3)])
        self.assertEqual(res["rows"][0]["percentile"], 100.0)
        self.assertEqual(res["rows"][2]["percentile"], round(100 / 3, 2))
        # not published yet: candidate sees a waiting message
        with self.db.read() as c:
            self.assertFalse(results.candidate_result(c, self.user("C3"), a3["id"])["available"])
        with self.assertRaisesRegex(ValidationError, "still open"):
            self.tx(results.publish, self.admin, self.sched["id"])
        clock.advance(8000)
        out = self.tx(results.publish, self.admin, self.sched["id"])
        self.assertEqual(out["candidates"], 3)
        with self.db.read() as c:
            r = results.candidate_result(c, self.user("C1"), a1["id"])
            self.assertEqual((r["rank"], r["appeared"]), (2, 3))
            self.assertEqual(len(r["review"]), len(self.q))
            pdf = results.report_card_pdf(c, self.user("C1"), a1["id"])
            csv_text = results.results_csv(c, self.sched["id"])
            an = results.analytics(c, self.sched["id"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn("C2", csv_text)
        self.assertEqual(an["appeared"], 3)
        self.assertEqual(sum(b["count"] for b in an["distribution"]), 3)

    def test_other_candidate_cannot_read_result(self):
        a = self.finish("C1", [])
        with self.db.read() as c, self.assertRaises(Forbidden):
            results.candidate_result(c, self.user("C2"), a["id"])

    def test_monitor_and_dashboard(self):
        self.start("C1")
        with self.db.read() as c:
            m = results.monitor(c, self.sched["id"])
            d = results.dashboard(c)
        self.assertEqual(len(m["attempts"]), 1)
        self.assertEqual(len(m["not_started"]), 2)
        self.assertEqual(d["in_progress"], 1)


if __name__ == "__main__":
    unittest.main()
