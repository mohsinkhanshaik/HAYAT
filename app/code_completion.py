"""Check and join generated code without treating a code fence as proof of success."""
from __future__ import annotations

import ast
import re


_FENCE = re.compile(r"(?m)^[ \t]*```([^\n`]*)[ \t]*$")
_CODE_TASK = re.compile(
    r"(?is)\b(?:write|create|generate|produce|provide|implement|build|give me|show me)\b"
    r".{0,160}\b(?:python|program|script|source code)\b"
)
_MAX_LINES = re.compile(r"(?i)\b(?:max(?:imum)?|at most|no more than)\s+(\d+)\s+lines?\b")


def code_task(question: str) -> bool:
    return bool(_CODE_TASK.search(question or ""))


def _prints_output(name: str, tree: ast.AST, owners: set[str]) -> bool:
    """A process stream must reach output; sys.stderr alone is not that stream."""
    for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
        printer = isinstance(call.func, ast.Name) and call.func.id == "print"
        writer = isinstance(call.func, ast.Attribute) and call.func.attr == "write"
        if not printer and not writer:
            continue
        for argument in call.args:
            if any(isinstance(item, ast.Attribute) and item.attr == name
                   and isinstance(item.value, ast.Name)
                   and item.value.id in owners
                   for item in ast.walk(argument)):
                return True
    return False


def _branch_roles(test: ast.AST, owners: set[str]) -> bool | None:
    """True means the if body is success; False means it is failure."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        role = _branch_roles(test.operand, owners)
        return None if role is None else not role
    if isinstance(test, ast.Attribute) and test.attr == "returncode" and isinstance(test.value, ast.Name):
        return False if test.value.id in owners else None
    if isinstance(test, ast.Compare) and len(test.ops) == len(test.comparators) == 1:
        left, right = test.left, test.comparators[0]
        if (isinstance(left, ast.Attribute) and left.attr == "returncode"
                and isinstance(left.value, ast.Name) and left.value.id in owners
                and isinstance(right, ast.Constant) and right.value == 0):
            if isinstance(test.ops[0], ast.Eq):
                return True
            if isinstance(test.ops[0], ast.NotEq):
                return False
    return None


def _subprocess_issue(tree: ast.AST, question: str) -> tuple[str, str]:
    """Check explicit subprocess requirements without executing generated code."""
    request = question.casefold()
    if "subprocess.run" not in request:
        return "", ""
    calls = [item for item in ast.walk(tree) if isinstance(item, ast.Call)
             and isinstance(item.func, ast.Attribute) and item.func.attr == "run"
             and isinstance(item.func.value, ast.Name) and item.func.value.id == "subprocess"]
    if not calls:
        return "missing_subprocess", "the requested subprocess.run call is missing"
    owners = {target.id for item in ast.walk(tree)
              if isinstance(item, ast.Assign) and item.value in calls
              for target in item.targets if isinstance(target, ast.Name)}
    if not owners:
        return "missing_result", "the program does not retain the subprocess result for checking"
    if re.search(r"\b(?:capture|print|display|show)\b.{0,25}\b(?:stdout|stderr|output)\b", request):
        captured = any(any(key.arg == "capture_output" and isinstance(key.value, ast.Constant)
                           and key.value.value is True for key in call.keywords)
                       or {"stdout", "stderr"} <= {key.arg for key in call.keywords}
                       for call in calls)
        if not captured:
            return "uncaptured_output", "subprocess.run does not capture stdout and stderr"
    timeout = re.search(r"\b(\d+)(?:[ -]second|\s*s(?:ec)?)\s+timeout\b", request)
    if timeout:
        wanted = int(timeout.group(1))
        if not any(any(key.arg == "timeout" and isinstance(key.value, ast.Constant)
                           and key.value.value == wanted for key in call.keywords) for call in calls):
            return "missing_timeout", f"subprocess.run needs timeout={wanted}"
    if "stdout" in request and not _prints_output("stdout", tree, owners):
        return "missing_stdout", "the program does not print captured stdout"
    if "stderr" in request and not _prints_output("stderr", tree, owners):
        return "missing_stderr", "the program does not print captured stderr on failure"
    if "stderr" in request and re.search(r"\b(?:fail(?:s|ure|ed)?|nonzero|non-zero|return code)\b", request):
        checked = False
        for item in (node for node in ast.walk(tree) if isinstance(node, ast.If)):
            success_body = _branch_roles(item.test, owners)
            if success_body is None:
                continue
            success = item.body if success_body else item.orelse
            failure = item.orelse if success_body else item.body
            if (any(_prints_output("stdout", node, owners) for node in success)
                    and any(_prints_output("stderr", node, owners) for node in failure)):
                checked = True
                break
        if not checked:
            return "missing_failure_branch", "success and failure branches must print the matching process streams"
    handlers = {ast.unparse(item.type) for item in ast.walk(tree)
                if isinstance(item, ast.ExceptHandler) and item.type is not None}
    if "filenotfounderror" in request and "FileNotFoundError" not in handlers:
        return "missing_file_error", "the program does not handle FileNotFoundError"
    if "timeoutexpired" in request and "subprocess.TimeoutExpired" not in handlers:
        return "missing_timeout_error", "the program does not handle subprocess.TimeoutExpired"
    return "", ""


def code_issue(answer: str, question: str = "") -> tuple[str, str]:
    """Return a structural issue and its reason, or two empty strings."""
    body = str(answer or "")
    fences = list(_FENCE.finditer(body))
    if len(fences) % 2:
        return "open_fence", "the code block is still open"
    requested = code_task(question)
    strict = bool(re.search(r"(?i)\b(?:one|single|exactly one)\s+python\s+code\s+block\b", question))
    if requested and not fences:
        # One-line expressions are valid answers without a fenced program.
        # Parse and validate them through the same checks as fenced Python.
        if not strict and re.search(r"(?i)\b(?:one[- ]liner|single[- ]line|one[- ]line)\b", question):
            source = body.strip().strip("`").strip()
            if source and "\n" not in source:
                return code_issue("```python\n" + source + "\n```", question)
        return "missing_program", "the requested program was not supplied"
    if strict and len(fences) != 2:
        return "block_count", "the request requires exactly one Python code block"
    if strict and fences:
        outside = body[:fences[0].start()] + body[fences[-1].end():]
        if outside.strip():
            return "extra_text", "the request permits only a Python code block"
    for opening, closing in zip(fences[::2], fences[1::2], strict=True):
        language = opening.group(1).strip().casefold()
        if language not in {"python", "py"}:
            continue
        source = body[opening.end():closing.start()].strip("\n")
        if not source.strip():
            return "empty_program", "the Python code block is empty"
        if requested:
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                return "invalid_python", f"Python syntax is incomplete at line {exc.lineno}"
            if re.search(r"(?i)\b(?:one[- ]liner|single[- ]line|one[- ]line)\b", question) and (len(source.splitlines()) != 1 or len(tree.body) != 1):
                return "one_liner_length", "the request requires one Python line and one statement using the supplied input"
            issue, reason = _subprocess_issue(tree, question)
            if issue:
                return issue, reason
            maximum = _MAX_LINES.search(question)
            if maximum and len(source.splitlines()) > int(maximum.group(1)):
                return "too_many_lines", f"the program exceeds {maximum.group(1)} lines"
            # "A one-liner that reverses a string s" was answered with
            # s = "hello"[::-1]: the user's s was replaced by a sample value.
            names = given_names(question)
            if names and not all(_uses_name(tree, name) for name in names):
                listed = ", ".join(names)
                return "ignored_names", f"the code never uses {listed} from the request"
    return "", ""


def merge_code_continuation(previous: str, addition: str) -> str:
    """Join inside an open fence without inserting prose whitespace into code."""
    first = previous
    second = addition
    second = re.sub(r"^```(?:python|py)?[ \t]*\n", "", second, count=1, flags=re.IGNORECASE)
    if first.endswith("\n") and second.startswith("\n"):
        second = second[1:]
    # Models sometimes repeat the partial line before finishing it. Keep the
    # repeated prefix only once while preserving the exact characters of code.
    for size in range(min(len(first), len(second)), 7, -1):
        if first[-size:] == second[:size]:
            second = second[size:]
            break
    return first + second


_GIVEN_NAME = re.compile(
    r"(?i)\b(?:string|str|list|array|number|integer|int|float|dict(?:ionary)?|set|tuple|variable|value|text|word|"
    r"sentence|path|file\s+name|filename|dataframe|df|matrix|n)\s+(?:called\s+|named\s+)?`?([a-z_][a-z0-9_]{0,15})`?"
    r"(?=\s*(?:$|[,.;:?!)]|\s+(?:and|or|to|into|in|of|with|from|that|which)\b))")
_NOT_NAMES = {"a", "an", "the", "it", "this", "that", "of", "in", "to", "is", "and", "or", "with", "from", "for",
              "into", "which", "python", "numbers", "strings", "values", "words", "items"}


def given_names(question: str) -> list[str]:
    """Names the request gives its inputs: "a string s", "a list called scores"."""
    return list(dict.fromkeys(name for name in _GIVEN_NAME.findall(question or "")
                              if name.lower() not in _NOT_NAMES))


def _uses_name(tree: ast.AST, name: str) -> bool:
    """The name is read somewhere (a parameter or a reference), not only assigned a sample value."""
    # Reading a sample after overwriting the input is still ignoring the input:
    # s = "hello"[::-1]; print(s) does not reverse the supplied s.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and any(
                isinstance(child, ast.arg) and child.arg == name for child in ast.walk(node.args)) and any(
                isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, ast.Load) for child in ast.walk(node)):
            return True
    rebound = False
    for statement in getattr(tree, "body", []):
        nodes = list(ast.walk(statement))
        if not rebound and any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load) for node in nodes):
            return True
        if any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store) for node in nodes):
            rebound = True
    return False


def repair_guidance(issue: str, reason: str) -> str:
    """Turn deterministic verification evidence into one bounded repair cue."""
    if issue == "ignored_names":
        return (f"Previous answer failed: {reason}. Write the code in terms of the names the user gave, "
                "as an expression or function over them, without replacing them with sample values.")
    if issue == "one_liner_length":
        return f"Previous answer failed: {reason}. Return a single expression using the input names from the request, not a sample program."
    if issue in {"uncaptured_output", "missing_stdout", "missing_stderr", "missing_failure_branch"}:
        return (
            f"Previous answer failed: {reason}. In subprocess.run use capture_output=True "
            "and text=True. Check returncode. Print result.stdout on success and "
            "result.stderr on a nonzero return code. Handle the named exceptions separately."
        )
    return f"Previous answer failed: {reason}. Regenerate the entire correct program."
