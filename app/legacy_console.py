from __future__ import annotations


def main() -> None:
    from features.fishing import engine

    try:
        engine.run()
    except KeyboardInterrupt:
        print("\n[EXIT] interrupted by user")
    except Exception:
        engine.add_user_log("상태 확인 필요", "error")
        raise
    finally:
        engine.request_roi_selection_cancel(True)
        if engine.overlay_controller is not None:
            engine.overlay_controller.cancel_roi_selection()
        engine.clear_current_fishing_search_roi()
        engine.stop_session_stats()
        engine._flush_run_time_once()
        engine.save_fishing_stats()
        if engine.overlay_controller is not None:
            engine.overlay_controller.stop()


if __name__ == "__main__":
    main()
