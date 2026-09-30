"""Demo data: a PW batch, candidates, a question bank and exams (one live now)."""
from . import clock, exams, questions, users

DEMO_ADMIN = ("admin", "Admin@2026")
DEMO_PASSWORD = "Exam@2026"

QUESTIONS = [
    ("Physics", "Kinematics", "easy", "A car accelerates uniformly from rest to 20 m/s in 5 s. Its acceleration is:",
     ["2 m/s²", "4 m/s²", "5 m/s²", "100 m/s²"], [1], "a = Δv/t = 20/5 = 4 m/s²"),
    ("Physics", "Laws of Motion", "medium", "Which of these are vector quantities?",
     ["Velocity", "Speed", "Force", "Mass"], [0, 2], "Velocity and force have direction."),
    ("Physics", "Work & Energy", "medium", "The SI unit of power is:", ["Joule", "Watt", "Newton", "Pascal"], [1], None),
    ("Physics", "Optics", "hard", "A convex lens of focal length 20 cm forms a real image at 60 cm. The object distance is:",
     ["15 cm", "30 cm", "40 cm", "60 cm"], [1], "1/f = 1/v − 1/u → u = −30 cm"),
    ("Chemistry", "Atomic Structure", "easy", "The atomic number of carbon is:", ["4", "6", "8", "12"], [1], None),
    ("Chemistry", "Periodic Table", "medium", "Which element has the highest electronegativity?",
     ["Oxygen", "Chlorine", "Fluorine", "Nitrogen"], [2], None),
    ("Chemistry", "Bonding", "medium", "Which molecules are non-polar?", ["CO₂", "H₂O", "CH₄", "NH₃"], [0, 2],
     "Symmetric geometry cancels the dipoles."),
    ("Chemistry", "Mole Concept", "hard", "Number of moles in 36 g of water:", ["1", "2", "3", "0.5"], [1], "36/18 = 2"),
    ("Mathematics", "Algebra", "easy", "If 2x + 3 = 11, then x =", ["3", "4", "5", "7"], [1], None),
    ("Mathematics", "Trigonometry", "medium", "sin²θ + cos²θ equals:", ["0", "1", "2", "tan θ"], [1], None),
    ("Mathematics", "Calculus", "medium", "d/dx (x³) =", ["x²", "3x²", "3x", "x³/3"], [1], None),
    ("Mathematics", "Probability", "hard", "Two fair dice are thrown. Probability that the sum is 7:",
     ["1/6", "1/12", "5/36", "7/36"], [0], "6 favourable outcomes of 36."),
    ("Biology", "Cell", "easy", "The powerhouse of the cell is the:", ["Nucleus", "Ribosome", "Mitochondrion", "Golgi body"],
     [2], None),
    ("Biology", "Genetics", "medium", "DNA is made of which of these?", ["Nucleotides", "Amino acids", "Fatty acids",
                                                                         "Monosaccharides"], [0], None),
    ("General Aptitude", "Series", "easy", "Next number: 2, 6, 12, 20, 30, ?", ["40", "42", "44", "36"], [1], "n(n+1)"),
    ("General Aptitude", "Reasoning", "medium", "If all Bloops are Razzies and all Razzies are Lazzies, then:",
     ["All Bloops are Lazzies", "All Lazzies are Bloops", "No Bloop is a Lazzie", "Cannot be determined"], [0], None),
]


def load_demo(db):
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM batches WHERE name='PW Batch 2026-A'").fetchone():
            return None
        users.ensure_admin(conn, *DEMO_ADMIN)
        admin = dict(conn.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone())
        users.create_staff(conn, admin, {"role": "examiner", "login_id": "examiner", "name": "Priya Examiner",
                                         "password": "Setter@2026"})
        users.create_staff(conn, admin, {"role": "proctor", "login_id": "proctor", "name": "Rahul Invigilator",
                                         "password": "Proctor@2026"})
        batch = users.save_batch(conn, admin, {"name": "PW Batch 2026-A", "description": "JEE/NEET foundation batch"})
        names = ["Aarav Sharma", "Diya Patel", "Kabir Singh", "Ananya Iyer", "Vihaan Gupta", "Ishita Reddy",
                 "Arjun Nair", "Meera Joshi"]
        for i, n in enumerate(names, 1):
            users.create_candidate(conn, admin, {"batch_id": batch["id"], "login_id": f"PW{i:03d}", "name": n,
                                                 "email": f"pw{i:03d}@example.com", "password": DEMO_PASSWORD})
        for subject, topic, diff, text, opts, correct, expl in QUESTIONS:
            questions.save_question(conn, admin, {
                "subject": subject, "topic": topic, "difficulty": diff, "text": text, "options": opts,
                "correct": correct, "qtype": "multi" if len(correct) > 1 else "single",
                "marks": 4, "negative": 1, "explanation": expl})
        qids = [r[0] for r in conn.execute("SELECT id FROM questions ORDER BY id")]
        mock = exams.save_exam(conn, admin, {
            "title": "PW Weekly Mock Test 1", "description": "Physics, Chemistry, Mathematics, Biology & Aptitude",
            "instructions": "Each question carries 4 marks; 1 mark is deducted for a wrong answer.\n"
                            "Multiple-answer questions need all correct options selected.\n"
                            "Do not switch tabs or exit full screen — the exam is auto-submitted after 3 warnings.",
            "duration_min": 30, "pass_percent": 40, "selection_mode": "fixed", "question_ids": qids,
            "max_violations": 3})
        exams.save_exam(conn, admin, {
            "title": "PW Random Practice Set", "duration_min": 15, "selection_mode": "random",
            "show_result": "immediate", "random_rules": [{"subject": "Physics", "count": 2},
                                                          {"subject": "Mathematics", "count": 2},
                                                          {"difficulty": "easy", "count": 2}]})
        now = clock.now()
        exams.save_schedule(conn, admin, {"exam_id": mock["id"], "batch_id": batch["id"],
                                          "starts_at": now - 600, "ends_at": now + 6 * 3600})
    return {"admin": DEMO_ADMIN[0] + " / " + DEMO_ADMIN[1], "candidates": f"PW001–PW008 / {DEMO_PASSWORD}",
            "examiner": "examiner / Setter@2026", "proctor": "proctor / Proctor@2026"}
