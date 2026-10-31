import arcpy
import csv
from pathlib import Path
from collections import defaultdict

arcpy.env.overwriteOutput = True
arcpy.env.addOutputsToMap = False
arcpy.env.outputCoordinateSystem = arcpy.SpatialReference(2193)

project = arcpy.mp.ArcGISProject("CURRENT")
folder = Path(project.filePath).parent / "Hamilton_Portfolio_V2"
gdb = str(folder / "Hamilton_Analysis.gdb")

parcels = str(Path(gdb) / "parcels_screening")
zoning = str(Path(gdb) / "zoning_clipped")

map_obj = project.listMaps("Hamilton_Residential_Screening")[0]
flood = next(
    layer for layer in map_obj.listLayers()
    if layer.name == "Hamilton_Illustrative_Flood_V2"
)

for dataset in (parcels, zoning, flood):
    if not arcpy.Exists(dataset):
        raise FileNotFoundError(f"Dataset not found: {dataset}")

zone_overlay = r"memory\zone_overlap"
flood_overlay = r"memory\flood_overlap"

try:
    arcpy.analysis.Intersect([parcels, zoning], zone_overlay)
    arcpy.analysis.Intersect([parcels, flood], flood_overlay)

    zone_areas = defaultdict(lambda: defaultdict(float))
    zone_subareas = defaultdict(lambda: defaultdict(float))
    with arcpy.da.SearchCursor(
        zone_overlay, ["SCREEN_ID", "Zoning_1", "Sub_Zone", "SHAPE@AREA"]
    ) as rows:
        for pid, zone, subzone, area in rows:
            pid = str(pid)
            zone = zone or "Unknown"
            subzone = subzone or "Unspecified"
            zone_areas[pid][zone] += area
            zone_subareas[pid][(zone, subzone)] += area

    flood_areas = defaultdict(lambda: defaultdict(float))
    with arcpy.da.SearchCursor(
        flood_overlay, ["SCREEN_ID", "RISK_CAT", "SHAPE@AREA"]
    ) as rows:
        for pid, category, area in rows:
            if category:
                flood_areas[str(pid)][str(category).upper()] += area

    output = folder / "deliverables" / "parcel_screening_code_demo.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow([
            "Parcel_ID", "Gross_m2", "Within_Study_m2",
            "Dominant_Zone", "Sub_Zone", "Residential_Pct",
            "Zoning_Cover_Pct", "Screening_Class",
            "Synthetic_High_Pct", "Synthetic_Moderate_Pct",
            "Synthetic_Low_Pct", "Synthetic_Total_Pct", "QA_Flag"
        ])

        with arcpy.da.SearchCursor(
            parcels, ["SCREEN_ID", "GROSS_M2", "IN_STUDY_M2"]
        ) as rows:
            for pid, gross, study_area in rows:
                pid = str(pid)
                study_area = float(study_area or 0)
                if study_area <= 0:
                    continue

                zone_values = zone_areas[pid]
                dominant = max(zone_values, key=zone_values.get) if zone_values else "Unknown"
                sub_values = {
                    sub: area for (zone, sub), area in zone_subareas[pid].items()
                    if zone == dominant
                }
                subzone = max(sub_values, key=sub_values.get) if sub_values else "Unspecified"

                residential = sum(
                    area for zone, area in zone_values.items()
                    if "residential" in zone.lower()
                )
                residential_pct = 100 * residential / study_area
                coverage_pct = 100 * sum(zone_values.values()) / study_area
                flood_pct = {
                    level: 100 * flood_areas[pid][level] / study_area
                    for level in ("HIGH", "MODERATE", "LOW")
                }
                total_flood = sum(flood_pct.values())

                if coverage_pct < 80 or coverage_pct > 105 or total_flood > 100.5:
                    screening = "DATA_REVIEW"
                elif not 500 <= study_area <= 60000:
                    screening = "SIZE_REVIEW"
                elif residential_pct >= 80:
                    screening = "PRIMARY_REVIEW"
                elif residential_pct >= 50:
                    screening = "SECONDARY_REVIEW"
                else:
                    screening = "OTHER_ZONING"

                writer.writerow([
                    pid, round(float(gross or 0), 2), round(study_area, 2),
                    dominant, subzone, round(residential_pct, 2),
                    round(coverage_pct, 2), screening,
                    round(flood_pct["HIGH"], 2),
                    round(flood_pct["MODERATE"], 2),
                    round(flood_pct["LOW"], 2), round(total_flood, 2),
                    "CHECK_OVERLAP" if total_flood > 100.5 else ""
                ])

    print("Results saved to:", output)
    print("Flood percentages are synthetic demonstration values, not actual hazard data.")
finally:
    for temp in (zone_overlay, flood_overlay):
        if arcpy.Exists(temp):
            arcpy.management.Delete(temp)
