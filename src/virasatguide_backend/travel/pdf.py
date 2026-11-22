"""Printable itinerary cards, budget summary, and source appendix."""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

from .schemas import TripPlan

INK = colors.HexColor("#23332D")
RUST = colors.HexColor("#A44728")
CREAM = colors.HexColor("#F5F1E8")


def render_plan_pdf(plan: TripPlan) -> bytes:
    # ReportLab bundles Vera, so exported PDFs have consistent embedded fonts.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    if "Virasat" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Virasat", str(font_dir / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("VirasatBold", str(font_dir / "VeraBd.ttf")))
    body = ParagraphStyle("TravelBody", fontName="Virasat", fontSize=9, leading=14,
                          textColor=INK, spaceAfter=7, splitLongWords=True)
    small = ParagraphStyle("TravelSmall", parent=body, fontSize=8, leading=12)
    title = ParagraphStyle("TravelTitle", parent=body, fontName="VirasatBold", fontSize=25, leading=31, spaceAfter=13)
    heading = ParagraphStyle("TravelHeading", parent=body, fontName="VirasatBold", fontSize=14, leading=19, spaceBefore=15, spaceAfter=10)
    label = ParagraphStyle("TravelLabel", parent=small, fontName="VirasatBold", textColor=RUST)

    stop_title = ParagraphStyle("StopTitle", parent=body, fontName="VirasatBold", fontSize=11)

    def paragraph(text, style=body):
        return Paragraph(escape(str(text)), style)

    def format_money(amount):
        return f"{plan.request.currency} {amount:,}"

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, A4[1] - 40, A4[0], 40, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("VirasatBold", 10)
        canvas.drawString(42, A4[1] - 25, "VIRASAT / YOUR TRAVEL PLAN")
        canvas.setFillColor(INK)
        canvas.setFont("Virasat", 8)
        canvas.drawString(42, 25, "Generated itinerary / estimates subject to change")
        canvas.drawRightString(A4[0] - 42, 25, f"{doc.page}")
        canvas.restoreState()

    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=62, bottomMargin=48, title=plan.title, author="VirasatGuide")
    content = [paragraph(plan.request.destination.upper(), label), paragraph(plan.title, title), paragraph(plan.summary)]
    location = " / ".join(v for v in [plan.request.area, plan.request.city] if v)
    if location:
        content.append(paragraph(location, label))
    if plan.request.origin or plan.request.stops:
        route = ([plan.request.origin.label] if plan.request.origin else [])
        route += [place.label for place in plan.request.stops] + [plan.request.destination]
        content.append(paragraph("Route: " + " -> ".join(route), small))
    if plan.request.departure_date:
        dates = f"Departure: {plan.request.departure_date.isoformat()}"
        dates += f" / Return: {plan.request.return_date.isoformat()}" if plan.request.return_date else " / One-way journey"
        content.append(paragraph(dates, small))
    if plan.request.travel_mode:
        content.append(paragraph("Preferred transport: " + plan.request.travel_mode, small))
    if plan.request.hotel_preferences:
        preferences = plan.request.hotel_preferences
        stars = ", ".join(str(v) for v in preferences.star_ratings) or "any"
        content.append(paragraph(f"Hotel preference: {preferences.rooms} room(s), stars: {stars}", small))
    facts = [[paragraph("DURATION", label), paragraph("TRAVELERS", label), paragraph("ESTIMATED TOTAL", label)],
             [paragraph(f"{plan.request.days} day(s)"), paragraph(plan.request.travelers), paragraph(format_money(plan.estimated_total))]]
    table = Table(facts, colWidths=[145, 145, doc.width - 290], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CREAM),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    content += [Spacer(1, 10), table, Spacer(1, 9)]
    if plan.request.budget is None:
        budget = "No budget specified"
    else:
        budget = f"Budget {format_money(plan.request.budget)} / {plan.budget_status.replace('_', ' ')}"
    if plan.cost_summary and not plan.cost_summary.costs_complete:
        budget += " / Incomplete estimate: some journey or stay costs are unknown"
    content.append(paragraph(budget, small))
    if plan.request.location:
        content.append(paragraph(f"Search location: {plan.request.location.latitude}, {plan.request.location.longitude}. Distances unverified.", small))
    content.append(paragraph("Costs cover all travelers. Stop costs, transport and accommodation are counted separately.", small))
    if plan.selected_offers:
        content.append(paragraph("Selected provider offers", heading))
        offers = plan.selected_offers
        if offers.transport:
            offer = offers.transport
            price = f"{format_money(offer.price.amount)} ({offer.price.type} quote)" if offer.price else "Price unavailable"
            content.append(paragraph(f"Transport: {offer.provider} / {offer.service_name} / {price}"))
            content.append(paragraph(f"Retrieved: {offer.retrieved_at.isoformat()}", small))
            if offer.booking_url:
                content.append(paragraph(offer.booking_url, small))
        if offers.hotel:
            offer = offers.hotel
            price = f"{format_money(offer.total_price.amount)} ({offer.total_price.type} stay total)" if offer.total_price else "Stay total unavailable"
            content.append(paragraph(f"Accommodation: {offer.hotel_name} / {offer.provider} / {price}"))
            content.append(paragraph(f"Stay: {offers.hotel_check_in} to {offers.hotel_check_out} / Rooms: {offers.hotel_rooms} / Retrieved: {offer.retrieved_at.isoformat()}", small))
            if offer.booking_url:
                content.append(paragraph(offer.booking_url, small))
        if not offers.transport and not offers.hotel:
            content.append(paragraph("No provider offers selected. Itinerary costs are estimates.", small))
    if plan.cost_summary:
        costs = plan.cost_summary
        content.append(paragraph(f"Confirmed provider quote total: {format_money(costs.confirmed_total)} / Itinerary estimate: {format_money(costs.itinerary_estimate)} / Selected provider estimates: {format_money(costs.selected_estimate_total)}", small))
        content.append(paragraph("Quote totals are saved snapshots; final prices and availability must be checked with each provider.", small))
    for day in plan.days:
        content.append(paragraph(f"Day {day.day} - {day.title}", heading))
        for index, stop in enumerate(day.stops, 1):
            parts = [paragraph(f"{stop.time_of_day.upper()} / {stop.duration_minutes} MIN / {format_money(stop.estimated_cost)}", label),
                     paragraph(f"{index}. {stop.place_name}", stop_title),
                     paragraph(stop.area, small), paragraph(stop.description), paragraph("Sources: " + ", ".join(stop.evidence_ids), small)]
            card = Table([[parts]], colWidths=[doc.width], hAlign="LEFT")
            card.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD5C8")),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 13),
                                      ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            content += [KeepTogether([card]), Spacer(1, 8)]
        content.append(paragraph(f"Transport {format_money(day.transport_cost)} / Accommodation {format_money(day.accommodation_cost)} / Day total {format_money(day.estimated_total)}", label))
    if plan.tips:
        content.append(paragraph("Before you go", heading))
        content.extend(paragraph("- " + tip) for tip in plan.tips)
    content.append(paragraph("Planning notes", heading))
    content.extend(paragraph("- " + warning, small) for warning in plan.warnings)
    content.append(paragraph("Sources & references", heading))
    for source in plan.sources:
        content.append(paragraph(f"[{source.id}] {source.title}", label))
        content.append(paragraph(source.text, small))
        if source.url:
            content.append(paragraph(source.url, small))
    content.append(paragraph(f"Created {plan.created_at.strftime('%d %b %Y')} / Plan {plan.plan_id}", small))
    doc.build(content, onFirstPage=draw_page, onLaterPages=draw_page)
    return stream.getvalue()
