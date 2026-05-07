import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from crewai import Agent, Crew, LLM, Process, Task


def _gemini_api_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def build_llm() -> LLM:
    model = os.getenv("GEMINI_MODEL", "gemini/gemini-2.5-flash-lite")
    temperature = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
    return LLM(model=model, api_key=_gemini_api_key(), temperature=temperature)


def build_agents(llm: LLM) -> Dict[str, Agent]:
    money_manager = Agent(
        role="You are a 5 star investment portfolio manager",
        goal="Build a investment portfolio for the user based on the user provided input and feedback.",
        backstory="<<MONEY_MANAGER_BACKSTORY>>",
        llm=llm,
        allow_delegation=False,
    )
    return {"money_manager": money_manager}


def build_tasks(agents: Dict[str, Agent]) -> list[Task]:
    money_manager_task = Task(
        description=(
            'Take the user provided \
input as context and what user want.\
Assume the user wants growth till retirement\
 and then a risk-off income based strategy after \
 retirement. Make any assumptions \
you need to make beyond the user provided input. \
Then build 3 portfolios- conservative , moderate and \
aggresive but within limits,that satisfy the user provided criteria. Validate your \
suggested portfolio using backtesting, drawdown and \
monte carlo simulation. Clearly explain why you chose certain investments \
and how it helps the user. Explain your assumptions \
and the date range of the daa you used to do any analysis'
            "Conversation history:\n{conversation_history}\n\n"
            "User message:\n{user_message}\n\n"
            "Previous portfolio proposal:\n{previous_portfolio}\n\n"
            "User feedback:\n{user_feedback}\n"
        ),
        expected_output="<<MONEY_MANAGER_TASK_OUTPUT>>",
        agent=agents["money_manager"],
    )
    return [money_manager_task]


def build_crew() -> Crew:
    llm = build_llm()
    agents = build_agents(llm)
    tasks = build_tasks(agents)
    return Crew(
        agents=list(agents.values()),
        tasks=tasks,
        process=Process.sequential,
    )


@dataclass
class ChatSession:
    session_id: str
    history: List[Dict[str, str]] = field(default_factory=list)
    last_portfolio: Optional[str] = None


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, ChatSession] = {}

    def get(self, session_id: str) -> ChatSession:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ChatSession(session_id=session_id)
            return self._sessions[session_id]


SESSION_STORE = SessionStore()


def _format_history(history: List[Dict[str, str]]) -> str:
    if not history:
        return ""
    lines: List[str] = []
    for turn in history[-12:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def run_message(
    session_id: str,
    message: str,
    inputs: Optional[Dict[str, str]] = None,
) -> str:
    session = SESSION_STORE.get(session_id)
    session.history.append({"role": "user", "content": message})

    llm = build_llm()
    agents = build_agents(llm)

    conversation_context = _format_history(session.history)
    runtime_inputs = {
        "conversation_history": conversation_context,
        "user_message": message,
        "previous_portfolio": session.last_portfolio or "",
        "user_feedback": message,
    }
    if inputs:
        runtime_inputs.update(inputs)

    money_manager_task = Task(
        description="<<MONEY_MANAGER_TASK_DESCRIPTION>>",
        expected_output="<<MONEY_MANAGER_TASK_OUTPUT>>",
        agent=agents["money_manager"],
    )
    crew = Crew(
        agents=[agents["money_manager"]],
        tasks=[money_manager_task],
        process=Process.sequential,
    )
    try:
        output = crew.kickoff(inputs=runtime_inputs)
    except Exception as exc:
        output = f"Money manager error: {exc}"

    session.history.append({"role": "assistant", "content": str(output)})
    session.last_portfolio = str(output)
    return str(output)


def run(inputs: Optional[Dict[str, str]] = None) -> str:
    crew = build_crew()
    return crew.kickoff(inputs=inputs or {})


if __name__ == "__main__":
    output = run()
    print(output)

