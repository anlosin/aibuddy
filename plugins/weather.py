"""天气查询插件 — Open-Meteo 主源（国内可达）+ wttr.in 回退"""
import json
import urllib.request
import urllib.parse

PLUGIN_INFO = {
    "name": "weather",
    "description": "查询指定城市的实时天气和三日预报，数据来源 Open-Meteo / wttr.in",
    "version": "2.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气信息，包括当前天气（温度、湿度、风速、天气状况）和未来三天的气温范围。支持中文和英文城市名，如 '北京'、'上海'、'Tokyo'、'London'",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "要查询的城市名称，中英文均可"
                    }
                },
                "required": ["city"]
            }
        }
    },
]

# ── WMO 天气代码→中文描述映射（Open-Meteo 使用 WMO 代码） ──
WMO_CODES = {
    0: "晴天", 1: "基本晴", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "零星毛毛雨", 53: "毛毛雨", 55: "密集毛毛雨",
    56: "零星冻毛毛雨", 57: "冻毛毛雨",
    61: "零星小雨", 63: "小雨", 65: "中到大雨",
    66: "零星冻雨", 67: "冻雨",
    71: "零星小雪", 73: "小雪", 75: "中到大雪", 77: "米雪",
    80: "零星阵雨", 81: "阵雨", 82: "强阵雨",
    85: "零星阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷雨伴冰雹",
}


def _wmo_desc(code):
    try:
        return WMO_CODES.get(int(code), f"天气代码{code}")
    except (TypeError, ValueError):
        return f"天气代码{code}"


_WIND_DIRS = ["北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南",
              "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北"]


def _wind_dir_16pt(deg):
    """角度→16 方位（Open-Meteo 给角度值）"""
    try:
        idx = int((int(deg) % 360 + 11.25) // 22.5) % 16
        return _WIND_DIRS[idx]
    except (TypeError, ValueError):
        return "未知"


def _http_get_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Chat-Plugin/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 主源：Open-Meteo（免费无 key，国内直连可达） ──
def _get_via_open_meteo(city, timeout=10):
    """Open-Meteo：geocoding 中文城市名 → 当前天气 + 三日预报"""
    geo_url = ("https://geocoding-api.open-meteo.com/v1/search?"
               + urllib.parse.urlencode({"name": city, "count": 1, "language": "zh"}))
    geo = _http_get_json(geo_url, timeout)
    results = geo.get("results") or []
    if not results:
        return None
    g = results[0]
    lat, lon = g["latitude"], g["longitude"]

    fc_url = ("https://api.open-meteo.com/v1/forecast?"
              + urllib.parse.urlencode({
                  "latitude": lat, "longitude": lon,
                  "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                             "weather_code,wind_speed_10m,wind_direction_10m",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                  "timezone": "auto", "forecast_days": 3,
              }))
    data = _http_get_json(fc_url, timeout)
    cur = data.get("current", {})
    if not cur:
        return None

    temp = round(cur["temperature_2m"])
    feels = round(cur["apparent_temperature"])
    humidity = cur["relative_humidity_2m"]
    wind = round(cur["wind_speed_10m"])
    wdir = _wind_dir_16pt(cur["wind_direction_10m"])
    desc = _wmo_desc(cur["weather_code"])

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    codes = daily.get("weather_code", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    forecast_parts = []
    for i in range(min(3, len(dates))):
        forecast_parts.append(
            f"{dates[i]}: {_wmo_desc(codes[i]) if i < len(codes) else '?'}  "
            f"{round(lows[i])}°C ~ {round(highs[i])}°C")

    result = (
        f"[{city}]\n"
        f"--------------------\n"
        f"当前: {desc}  {temp}°C (体感 {feels}°C)\n"
        f"湿度: {humidity}%  风速: {wind}km/h {wdir}\n"
        f"\n未来三天:\n" + "\n".join(forecast_parts)
    )
    return result


# ── 回退：wttr.in（海外源，供代理/海外环境） ──
WEATHER_CODES = {
    "113": "晴天", "116": "晴间多云",
    "119": "多云", "122": "阴",
    "143": "雾", "176": "零星阵雨",
    "179": "零星阵雪", "182": "零星雨夹雪",
    "185": "零星冻雨", "200": "局部雷阵雨",
    "227": "零星雪", "230": "暴风雪",
    "248": "雾", "260": "冻雾",
    "263": "零星毛毛雨", "266": "毛毛雨",
    "281": "零星冻毛毛雨", "284": "冻毛毛雨",
    "293": "零星小雨", "296": "小雨",
    "299": "局部中雨", "302": "中雨",
    "305": "局部大雨", "308": "大雨",
    "311": "零星冻雨", "314": "冻雨",
    "317": "零星雨夹雪", "320": "雨夹雪",
    "323": "零星小雪", "326": "小雪",
    "329": "局部中雪", "332": "中雪",
    "335": "局部大雪", "338": "大雪",
    "350": "冰雹", "353": "零星雷阵雨",
    "356": "局部雷阵雨", "359": "雷暴",
    "362": "零星雷雨夹雪", "365": "局部雷雨夹雪",
    "368": "零星雷雪", "371": "局部雷雪",
    "374": "零星冰粒", "377": "冰粒",
    "386": "局部雷阵雨", "389": "局部雷暴",
    "392": "零星雷雨夹雪", "395": "局部雷雨夹雪",
}


def _translate_desc(code):
    return WEATHER_CODES.get(code, f"天气代码{code}")


def _get_via_wttr(city, timeout=10):
    """wttr.in 回退（2026-09 曾出现证书过期故障，仅作备源）"""
    encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{encoded}?format=j1"
    data = _http_get_json(url, timeout)

    cur = data["current_condition"][0]
    temp = cur["temp_C"]
    feels = cur["FeelsLikeC"]
    humidity = cur["humidity"]
    wind = cur["windspeedKmph"]
    wind_dir = cur["winddir16Point"]
    desc = _translate_desc(cur["weatherCode"])
    vis = cur["visibility"]

    forecast_parts = []
    for day in data["weather"][:3]:
        date = day["date"]
        high = day["maxtempC"]
        low = day["mintempC"]
        fcode = day["hourly"][4]["weatherCode"]
        fdesc = _translate_desc(fcode)
        forecast_parts.append(f"{date}: {fdesc}  {low}°C ~ {high}°C")

    forecast = "\n".join(forecast_parts)
    return (
        f"[{city}]\n"
        f"--------------------\n"
        f"当前: {desc}  {temp}°C (体感 {feels}°C)\n"
        f"湿度: {humidity}%  风速: {wind}km/h {wind_dir}  能见度: {vis}km\n"
        f"\n未来三天:\n{forecast}"
    )


def execute(name, arguments):
    if name != "get_weather":
        return f"未知工具: {name}"

    city = arguments.get("city", "")
    if not city:
        return "错误：未指定城市名"

    try:
        result = _get_via_open_meteo(city)
        if result:
            return result
        return f"未找到城市「{city}」，请检查城市名是否正确"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"未找到城市「{city}」，请检查城市名是否正确"
        # Open-Meteo 出问题 → 回退 wttr.in
        return _fallback(city, f"主源 HTTP {e.code}")
    except Exception:
        return _fallback(city, "主源不可用")


def _fallback(city, reason):
    """Open-Meteo 失败时回退 wttr.in"""
    try:
        result = _get_via_wttr(city)
        if result:
            return result
    except Exception:
        pass
    return (f"查询失败（{reason}）。\n"
            f"提示：主源 Open-Meteo 与备源 wttr.in 均无法访问，"
            f"当前可能处于内网/离线环境。")
