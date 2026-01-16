axxela_market_sim/
├── engine/                      # DOMAIN LAYER (pure logic)
│   ├── __init__.py
│   ├── order_book.py           # CLOB implementation
│   ├── matching_engine.py      # Price-time priority matching
│   ├── trader.py               # Trader entity
│   ├── bot_strategies.py       # Bot AI logic
│   └── risk_manager.py         # Position limits, margin calls
│
├── application/                 # APPLICATION LAYER (orchestration)
│   ├── __init__.py
│   ├── market_simulator.py     # Main game loop (thread-safe)
│   ├── session_manager.py      # Round management
│   ├── analytics_engine.py     # PnL, Greeks, statistics
│   └── replay_manager.py       # Session recording/playback
│
├── infrastructure/              # INFRASTRUCTURE LAYER
│   ├── __init__.py
│   ├── config.py               # Difficulty settings
│   ├── persistence.py          # JSON storage
│   └── logger.py               # Structured logging
│
├── ui/                          # PRESENTATION LAYER (replaceable)
│   ├── __init__.py
│   ├── desktop/                # PyQt6 Desktop GUI
│   │   ├── main_window.py
│   │   ├── widgets/
│   │   │   ├── order_book_widget.py
│   │   │   ├── position_panel.py
│   │   │   ├── greeks_widget.py
│   │   │   └── chart_widget.py
│   │   └── styles.qss
│   │
│   ├── web/                    # FastAPI + React (future)
│   │   ├── backend/
│   │   └── frontend/
│   │
│   └── tui/                    # Textual (CLI replacement)
│       ├── app.py
│       └── widgets.py
│
├── tests/                       # Unit & integration tests
│   ├── test_matching_engine.py
│   ├── test_risk_manager.py
│   └── test_bots.py
│
├── requirements.txt
├── setup.py
└── README.md
