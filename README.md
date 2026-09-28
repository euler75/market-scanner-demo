# Multi-Threaded Real-Time Market Scanner & Telegram Alert Bot

A robust, cloud-ready Python application designed to continuously scan **cryptocurrency markets** and **major equities/stocks** across daily and weekly timeframes. It computes custom technical indicators and dispatches structured, real-time alerts directly via a Telegram Bot API.

## Key Features
* **Multi-Threaded Execution:** Runs independent concurrent background workers for crypto and stock symbol lists.
* **Technical Indicator Engine:** 
  * Relative Strength Index (`RSI-14`)
  * Awesome Oscillator (`AO`) with momentum coloring
  * Parabolic SAR (`PSAR`) trend-following filters
* **Cloud-Optimized (24/7 Uptime):** Includes a lightweight built-in HTTP health check server designed for seamless deployment on cloud platforms like **Render**, **Northflank**, or **Railway**.
* **Duplicate Alert Prevention:** State caching (`SENT_ALERTS`) prevents redundant notifications.

## Tech Stack
* **Language:** Python 3.10+
* **Libraries:** `requests`, `threading`, `http.server`, `logging`
* **APIs:** Yahoo Finance Chart API, Telegram Bot API
