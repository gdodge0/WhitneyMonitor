import calendar  # New import for building month grid
from flask import Blueprint, current_app, render_template, url_for, request
from .lib.common import *
from collections import defaultdict
from .lib.common import get_session  # updated logic uses Events directly
from sqlalchemy.orm import joinedload
from sqlalchemy import select
from providers.monitor.Base.tools.Statistics.lib.models import Event
from zoneinfo import ZoneInfo  # py3.9+
from datetime import timezone as _tz


def create_blueprint() -> Blueprint:
    """
    cfg is the dict piece from app.config['BLUEPRINT_CONFIG']['blog']
    """
    bp = Blueprint(
        "Statistics",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
        url_prefix="/stats"
    )

    bp.meta = {
        "name": "Statistics",
        "description": "Statistics about discovered items.",
        "image": "https://upload.wikimedia.org/wikipedia/commons/d/de/Gear-icon.png",
    }

    # -- on first load --
    @bp.record_once
    def on_register(state):
        app = state.app
        with app.app_context():
            init_sqlite_db(current_app.config["GLOBAL_CFG"].extra["sqlite"]["path"])

    # -- Views ------------------------------------------------
    @bp.route("/", methods=["GET"])
    def index():
        """Render statistics dashboard with optional query-string filters.

        Accepted query parameters:

        * ``source`` – monitor/provider name attached to :pyclass:`Event`.
        * ``name``   – exact Item ``name`` match.
        * ``code``   – exact Item ``code`` match.
        """

        # ---- Parse incoming filters --------------------------------------
        src_filter: str | None = request.args.get("source") or None
        name_filter: str | None = request.args.get("name") or None
        # New: parse optional minimum and maximum count range from query string
        count_min_param = request.args.get("count_min") or None
        count_max_param = request.args.get("count_max") or None
        # New: choose whether calendar aggregates by item entry date (default) or detection timestamp
        date_mode: str = request.args.get("date_mode", "entry")
        if date_mode not in ("entry", "detected"):
            date_mode = "entry"

        # Optional: specific day filter (YYYY-MM-DD) to narrow events shown
        day_filter: str | None = request.args.get("date") or None

        count_min: int | None = int(count_min_param) if count_min_param and count_min_param.isdigit() else None
        count_max: int | None = int(count_max_param) if count_max_param and count_max_param.isdigit() else None

        # User timezone (IANA) – detected client-side and round-tripped
        tz_label = request.args.get("tz", "UTC")
        try:
            tzinfo = ZoneInfo(tz_label)
        except Exception:
            tzinfo = ZoneInfo("UTC")
            tz_label = "UTC"

        item_filters = {}
        event_filters = {}
        if src_filter:
            event_filters["source"] = src_filter
        if name_filter:
            item_filters["name"] = name_filter
        # We will apply the numeric range on *Item.count* after retrieving the initial
        # result set, because the low-level helpers currently only support equality
        # filters. This keeps the change isolated to this view.

        # If we passed empty dicts, make them ``None`` so helpers skip faster.
        if not item_filters:
            item_filters = None  # type: ignore
        if not event_filters:
            event_filters = None  # type: ignore

        # ---- Query + aggregate inside a live Session ----------------------

        items_by_date: defaultdict[str, int] = defaultdict(int)
        item_counts: defaultdict[tuple[str, str], int] = defaultdict(int)

        with get_session() as session:
            # ------------------------------------------------------------------
            # Build the initial Event query (source filter only) ----------------

            stmt = select(Event).options(joinedload(Event.items))
            if event_filters:
                for k, v in event_filters.items():
                    stmt = stmt.filter(getattr(Event, k) == v)

            events = session.scalars(stmt).unique().all()

            # Helper: choose the first (earliest) item to represent the event
            def first_item(ev):
                if not ev.items:
                    return None
                return min(ev.items, key=lambda it: it.timestamp or ev.timestamp)

            matched_events = []
            for ev in events:
                itm = first_item(ev)
                if itm is None:
                    continue

                # Name filter --------------------------------------------------
                if name_filter and itm.name != name_filter:
                    continue

                # Count range filters -----------------------------------------
                if (count_min is not None and itm.count < count_min) or (
                    count_max is not None and itm.count > count_max
                ):
                    continue

                # Optional date filter check ---------------------------------
                if day_filter:
                    # Compute comparable date string based on mode
                    if date_mode == "detected":
                        raw_dt = ev.timestamp
                        if raw_dt.tzinfo is None:
                            raw_dt = raw_dt.replace(tzinfo=_tz.utc)
                        cmp_date = raw_dt.astimezone(tzinfo).date().isoformat()
                    else:
                        cmp_date = itm.date.isoformat()
                    if cmp_date != day_filter:
                        continue

                matched_events.append((ev, itm))

            # 1) Detections aggregated by selected date basis -----------------
            for ev, itm in matched_events:
                if date_mode == "detected":
                    raw_dt = ev.timestamp
                    if raw_dt.tzinfo is None:
                        raw_dt = raw_dt.replace(tzinfo=_tz.utc)
                    date_str = raw_dt.astimezone(tzinfo).date().isoformat()
                else:  # "entry" – use the item/permit entry date
                    date_str = itm.date.isoformat()
                items_by_date[date_str] += 1

            # 2) Item detections + counts ------------------------------------
            item_detections: defaultdict[tuple[str, str], int] = defaultdict(int)
            for _ev, itm in matched_events:
                key = (itm.name, itm.code)
                item_counts[key] += itm.count  # highest stock (first item)
                item_detections[key] += 1

            # Percent distribution based on aggregated counts
            total_count = sum(item_counts.values())
            item_percent: list[tuple[tuple[str, str], float]] = []
            if total_count > 0:
                for key, cnt in item_counts.items():
                    item_percent.append((key, 100.0 * cnt / total_count))
            item_percent.sort(key=lambda kv: (-kv[1], kv[0][0]))

            percent_lookup = {k: p for k, p in item_percent}
            item_stats = [
                (key, item_detections[key], percent_lookup.get(key, 0.0))
                for key in item_detections
            ]
            item_stats.sort(key=lambda tup: (-tup[1], tup[0][0]))

            # 3) Distribution of count values (per event)
            from collections import Counter
            count_values = [itm.count for _ev, itm in matched_events]
            count_counter = Counter(count_values)
            dist_total = sum(count_counter.values())
            count_distribution = [
                (val, 100.0 * freq / dist_total)
                for val, freq in sorted(count_counter.items())
            ]

            # 4) Dropdown options --------------------------------------------
            all_ev = session.scalars(select(Event).options(joinedload(Event.items))).unique().all()
            sources = sorted({ev.source for ev in all_ev})
            names = sorted({first_item(ev).name for ev in all_ev if first_item(ev)})
            counts = sorted({first_item(ev).count for ev in all_ev if first_item(ev)})

            # 5) Heatmap ------------------------------------------------------
            heatmap = [[0 for _ in range(24)] for _ in range(7)]
            for ev, _itm in matched_events:
                raw_dt = ev.timestamp
                if raw_dt.tzinfo is None:
                    raw_dt = raw_dt.replace(tzinfo=_tz.utc)
                dt = raw_dt.astimezone(tzinfo)
                # Normalise to local time zone (server) – adjust if needed later
                dow = dt.weekday()  # 0=Mon … 6=Sun
                hour = dt.hour
                heatmap[dow][hour] += 1

            max_heat = max((v for row in heatmap for v in row), default=0)

        # ---- Build calendar grid for current month ---------------------
        from datetime import datetime
        now_local = datetime.now(tzinfo)
        year, month_num = now_local.year, now_local.month
        month_label = now_local.strftime("%B %Y")
        month_matrix = calendar.monthcalendar(year, month_num)

        month_grid: list[list[dict | None]] = []
        month_max = 0
        for week in month_matrix:
            week_cells: list[dict | None] = []
            for day in week:
                if day == 0:
                    week_cells.append(None)
                else:
                    date_str = f"{year}-{month_num:02d}-{day:02d}"
                    cnt = items_by_date.get(date_str, 0)
                    month_max = max(month_max, cnt)
                    week_cells.append({"day": day, "count": cnt})
            month_grid.append(week_cells)

        return render_template(
            "Statistics/index.html",
            items_by_date=sorted(items_by_date.items(), reverse=True),
            item_stats=item_stats,
            count_distribution=count_distribution,
            month_grid=month_grid,
            month_max=month_max,
            month_label=month_label,
            filters={
                "source": src_filter,
                "name": name_filter,
                "count_min": count_min,
                "count_max": count_max,
                "date_mode": date_mode,
                "date": day_filter,
            },
            options={
                "sources": sources,
                "names": names,
                "counts": counts,
            },
            heatmap=heatmap,
            heatmap_max=max_heat,
            tz_label=tz_label,
        )

    return bp
