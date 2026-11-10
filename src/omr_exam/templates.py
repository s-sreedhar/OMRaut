"""Ready-made test templates so a new test is one click, not a long form."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from omr_exam.test_builder import create_test_config


@dataclass(frozen=True)
class TestTemplate:
    key: str
    name: str
    description: str
    question_count: int
    options: tuple[str, ...] = ("A", "B", "C", "D")
    columns: int = 2
    set_codes: tuple[str, ...] = ("A",)
    roll_number_digits: int = 6
    sections: tuple[str, ...] = ()
    correct_points: float = 1
    incorrect_points: float = 0
    blank_points: float = 0

    def summary(self) -> str:
        negative = f", {self.incorrect_points:g} for wrong" if self.incorrect_points else ", no negative marking"
        return (f"{self.question_count} questions · options {', '.join(self.options)} · "
                f"{len(self.set_codes)} set(s) · +{self.correct_points:g} per correct{negative}")


TEMPLATES: tuple[TestTemplate, ...] = (
    TestTemplate("quick", "Quick Quiz", "10 questions, one set. Fast classroom check.", 10, columns=1),
    TestTemplate("class", "Class Test (25)", "25 questions, A–D, two sets to discourage copying.", 25,
                 columns=1, set_codes=("A", "B")),
    TestTemplate("standard", "Standard Exam (50)", "50 questions, A–D, two columns, sets A and B.", 50,
                 columns=2, set_codes=("A", "B")),
    TestTemplate("five", "Five Options (50)", "50 questions with options A–E.", 50,
                 options=("A", "B", "C", "D", "E"), columns=2, set_codes=("A", "B")),
    TestTemplate("truefalse", "True / False (20)", "20 statements answered T or F.", 20,
                 options=("T", "F"), columns=2),
    TestTemplate("large", "Large Exam (100)", "100 questions, three columns, sets A–D, negative marking.", 100,
                 columns=3, set_codes=("A", "B", "C", "D"), correct_points=4, incorrect_points=-1),
    TestTemplate("competitive", "Entrance Style (90)", "90 questions in 3 subjects, +4 / −1 marking.", 90,
                 columns=3, set_codes=("A", "B", "C", "D"), sections=("Physics", "Chemistry", "Maths"),
                 correct_points=4, incorrect_points=-1),
)


def get_template(key: str) -> TestTemplate:
    for template in TEMPLATES:
        if template.key == key:
            return template
    raise KeyError(key)


def build_from_template(template: TestTemplate, *, title: str, class_name: str, subject: str,
                        test_date: str | None = None, **overrides: Any) -> dict[str, Any]:
    """Create a validated test config from a template; any create_test_config argument can be overridden."""
    arguments: dict[str, Any] = dict(
        title=title, test_date=test_date or date.today().isoformat(), class_name=class_name, subject=subject,
        question_count=template.question_count, options=list(template.options),
        roll_number_digits=template.roll_number_digits, set_codes=list(template.set_codes),
        fields=["name", "class"], columns=template.columns,
        sections=list(template.sections) or None, correct_points=template.correct_points,
        incorrect_points=template.incorrect_points, blank_points=template.blank_points,
    )
    arguments.update(overrides)
    return create_test_config(**arguments)
