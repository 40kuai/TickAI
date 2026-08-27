"""
Modular Agent System - BaseAgent and utilities.
"""
from __future__ import annotations

import abc
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


class AgentContext:
    """
    Context object shared between agents in a chain.
    Stores state and intermediate results.
    """
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._history: List[Tuple[str, str]] = []  # (agent_name, event)

    def get(self, key: str, default: Any = None) -> Any:
        """Get value from context."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set value in context."""
        self._data[key] = value

    def append_history(self, agent_name: str, event: str) -> None:
        """Append event to execution history."""
        self._history.append((agent_name, event))

    @property
    def data(self) -> Dict[str, Any]:
        return dict(self._data)

    @property
    def history(self) -> List[Tuple[str, str]]:
        return list(self._history)


class BaseAgent(abc.ABC):
    """
    Base abstract class for all agents.
    Defines the common interface and lifecycle.
    """
    name: str = "base_agent"
    description: str = "Base agent"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.started_at: Optional[datetime] = None
        self.stopped_at: Optional[datetime] = None

    @abc.abstractmethod
    def run(self, context: AgentContext) -> AgentContext:
        """
        Core agent execution logic.
        Must be implemented by subclasses.
        """
        pass

    def pre_run(self, context: AgentContext) -> None:
        """Hook executed before run()."""
        self.started_at = datetime.utcnow()
        logger.debug(f"Agent {self.name} starting at {self.started_at}")

    def post_run(self, context: AgentContext) -> None:
        """Hook executed after run()."""
        self.stopped_at = datetime.utcnow()
        logger.debug(f"Agent {self.name} completed at {self.stopped_at}")

    def __call__(self, context: AgentContext) -> AgentContext:
        """
        Allow agent to be called like a function.
        """
        self.pre_run(context)
        result = self.run(context)
        self.post_run(result)
        return result


class AgentChain:
    """
    Execute a sequence of agents in order.
    Agents share the same AgentContext.
    """
    def __init__(self, agents: Optional[List[BaseAgent]] = None):
        self.agents: List[BaseAgent] = agents or []
        self._stop_on_error: bool = True

    def add_agent(self, agent: BaseAgent) -> "AgentChain":
        self.agents.append(agent)
        return self

    def stop_on_error(self, stop: bool = True) -> "AgentChain":
        self._stop_on_error = stop
        return self

    def run(self, initial_context: Optional[AgentContext] = None) -> AgentContext:
        """
        Run the chain of agents.
        """
        context = initial_context or AgentContext()

        for agent in self.agents:
            try:
                context.append_history(agent.name, "started")
                context = agent(context)
                context.append_history(agent.name, "completed")
            except Exception as exc:
                logger.error(f"Agent {agent.name} failed: {exc}")
                context.append_history(agent.name, f"error: {str(exc)}")
                if self._stop_on_error:
                    raise
        return context

    def __repr__(self):
        agent_names = [a.name for a in self.agents]
        return f"AgentChain(agents={agent_names})"
