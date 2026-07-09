
# Live Section Analyzer V3 Architecture

## Project Goal

Develop a fast, robust and modular real-time section analysis tool for FreeCAD.

The application must support large models, remain responsive while the clipping plane moves, and generate reliable section geometry.

---

# Design Principles

- Single Responsibility Principle
- Modular Architecture
- No duplicated code
- Separation of Geometry and Display
- Every module must be independently testable
- Performance first
- Readability before cleverness

---

# Overall Architecture

```
               UI
               │
               ▼
         Main Controller
               │
     ┌─────────┴─────────┐
     ▼                   ▼
Section Engine     Display Engine
     │                   │
     ▼                   ▼
Geometry Cache     Coin3D SceneGraph
     │
     ▼
Face Builder
     │
     ▼
Hatch Engine
```

---

# Modules

## main.py

Application entry point.

Responsibilities

- start application
- create dock widget
- initialize modules
- connect signals

Must NOT contain geometry calculations.

---

## ui.py

User interface only.

Responsibilities

- buttons
- sliders
- checkboxes
- color selectors

Must NOT perform calculations.

---

## section_engine.py

Heart of the application.

Responsibilities

- clipping plane
- plane transformation
- intersection calculation
- geometry generation

Must NOT know anything about the UI.

---

## display_engine.py

Visualization layer.

Responsibilities

- Coin3D nodes
- colors
- transparency
- edge drawing
- temporary objects

Must NOT calculate geometry.

---

## face_builder.py

Convert section wires into valid faces.

Responsibilities

- build faces
- repair invalid wires
- detect holes
- nested loops

---

## hatch_engine.py

Generate hatch patterns.

Responsibilities

- hatch spacing
- hatch angle
- clipping
- pattern generation

No UI code.

---

## cache.py

Performance cache.

Responsibilities

- cache shapes
- cache triangulation
- cache topology

---

## profiler.py

Performance measurements.

Responsibilities

- execution times
- FPS
- geometry statistics

---

# Data Flow

```
User moves slider

↓

UI

↓

Controller

↓

Section Engine

↓

Face Builder

↓

Display Engine

↓

Viewport update
```

---

# Coding Rules

## Maximum file size

300 lines

---

## Maximum function size

40 lines

---

## Maximum nesting

3 levels

---

## Every class has one responsibility

Good

```
SectionEngine
```

Bad

```
SectionEngineUICacheDisplayManager
```

---

# Performance Goals

Plane movement

Target:

60 FPS

Acceptable:

30 FPS

Minimum:

20 FPS

---

Section calculation

Small models

< 10 ms

Medium models

< 30 ms

Large models

< 100 ms

---

# Development Roadmap

## V0.1

Core engine

- clipping plane
- live update

---

## V0.2

Section edges

---

## V0.3

Face Builder

---

## V0.4

Solid fill

---

## V0.5

Hatch

---

## V0.6

Freeze

Mirror

Measurements

---

## V1.0

Stable release

Documentation

Unit tests

Examples

---

# Non Goals

The application is NOT intended to replace FreeCAD Part Section.

It focuses on interactive engineering visualization.

---

# Philosophy

Correctness

↓

Robustness

↓

Performance

↓

Features

A feature that reduces stability will not be merged.
