---
paths:
  - "mes_intel/agents/**/*.py"
---

# Agent Development Rules
- All agents must subscribe to TRADE_RESULT and LESSON_LEARNED events
- Use the event bus (mes_intel.event_bus.bus) for inter-agent communication
- Persist knowledge to SQLite via database.py (agent_knowledge table)
- MES tick size: 0.25 points = $1.25
- When modifying one agent, check if cross-agent events are affected
- MetaLearner orchestrates — don't bypass it for weight updates
