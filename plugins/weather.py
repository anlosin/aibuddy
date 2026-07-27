"""天气查询插件 — 基于 wttr.in 免费天气 API"""
import json
import urllib.request
import urllib.parse

PLUGIN_INFO = {
    "name": "weather",
    "description": "查询指定城市的实时天气和三日预报，数据来源 wttr.in",
    "version": "1.0",
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

# ── 天气代码→中文描述映射 ──
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


def execute(name, arguments):
    if name != "get_weather":
        return f"未知工具: {name}"

    city = arguments.get("city", "")
    if not city:
        return "错误：未指定城市名"

    try:
        encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Chat-Plugin/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # 当前天气
        cur = data["current_condition"][0]
        temp = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humidity = cur["humidity"]
        wind = cur["windspeedKmph"]
        wind_dir = cur["winddir16Point"]
        desc = _translate_desc(cur["weatherCode"])
        vis = cur["visibility"]

        # 未来三天预报
        forecast_parts = []
        for day in data["weather"][:3]:
            date = day["date"]
            high = day["maxtempC"]
            low = day["mintempC"]
            fcode = day["hourly"][4]["weatherCode"]  # 中午时段的天气代码
            fdesc = _translate_desc(fcode)
            forecast_parts.append(f"{date}: {fdesc}  {low}°C ~ {high}°C")

        forecast = "\n".join(forecast_parts)

        result = (
            f"[{city}]\n"
            f"--------------------\n"
            f"当前: {desc}  {temp}°C (体感 {feels}°C)\n"
            f"湿度: {humidity}%  风速: {wind}km/h {wind_dir}  能见度: {vis}km\n"
            f"\n未来三天:\n{forecast}"
        )
        return result

    except urllib.error.HTTPError as e:
        return f"查询失败: HTTP {e.code}，请检查城市名是否正确"
    except urllib.error.URLError as e:
        return (f"🌐 网络错误: {e.reason}\n"
                f"提示：当前可能处于内网/离线环境，天气服务(wttr.in)无法访问。")
    except Exception as e:
        return f"天气查询出错: {e}"
