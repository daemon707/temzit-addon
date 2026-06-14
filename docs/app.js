(()=>{
    "use strict";
    var e = {
        730: (e,t,r)=>{
            r.d(t, {
                l: ()=>s,
                X: ()=>d
            });
            var n, o = r(368), l = 0, a = 0, s = "https://service.temzit.ru", d = {}, i = 0, c = !1;
            function u(e, t, r={}) {
                let n = new Date(Date.now() + 864e8);
                n = n.toUTCString(),
                (r = {
                    path: "/",
                    expires: n
                }).expires instanceof Date && (r.expires = r.expires.toUTCString());
                let o = encodeURIComponent(e) + "=" + encodeURIComponent(t);
                for (let e in r) {
                    o += "; " + e;
                    let t = r[e];
                    !0 !== t && (o += "=" + t)
                }
                document.cookie = o
            }
            function m(e, t, r=1) {
                if (!e)
                    throw "No URL for getURL";
                try {
                    "function" == typeof t.operationComplete && (t = t.operationComplete)
                } catch (e) {}
                if ("function" != typeof t)
                    throw "No callback function for getURL";
                let n = null;
                if ("undefined" != typeof XMLHttpRequest)
                    n = new XMLHttpRequest;
                else if ("undefined" != typeof ActiveXObject)
                    try {
                        n = new ActiveXObject("Msxml2.XMLHTTP")
                    } catch (e) {
                        try {
                            n = new ActiveXObject("Microsoft.XMLHTTP")
                        } catch (e) {}
                    }
                if (!n)
                    throw "Both getURL and XMLHttpRequest are undefined";
                if (n.cmd = r,
                n.onreadystatechange = function() {
                    4 == n.readyState && (a = 0,
                    t({
                        success: !0,
                        content: n.responseText,
                        contentType: n.getResponseHeader("Content-Type"),
                        status: n.status,
                        cmd: n.cmd
                    }))
                }
                ,
                3 == r) {
                    let t = new FormData(document.forms.form_config);
                    t.append("submit", "true"),
                    n.open("POST", e, !0),
                    n.send(t)
                } else if (5 == r) {
                    let t = new FormData(document.forms.form_schedule);
                    t.append("submit", "true"),
                    n.open("POST", e, !0),
                    n.send(t)
                } else
                    n.open("GET", e, !0),
                    n.send(null)
            }
            function _() {
                let e = document.getElementById("link_green");
                e.style.display = c ? "" : "none",
                e = document.getElementById("link_red"),
                e.style.display = c ? "none" : ""
            }
            function p() {
                _();
                let e = localStorage.temzit_app_server_data;
                if (null == e || "" == e)
                    return;
                let t = JSON.parse(e)
                  , r = document.getElementById("Tin");
                r.innerHTML = t.t_indoor >= 0 ? "+" + t.t_indoor : t.t_indoor,
                r = document.getElementById("Tout"),
                r.innerHTML = t.t_outdoor >= 0 ? "+" + t.t_outdoor : t.t_outdoor,
                r = document.getElementById("Twater_supply"),
                r.innerHTML = t.t_water_supply >= 0 ? "+" + t.t_water_supply : t.t_water_supply,
                r = document.getElementById("Twater_return"),
                r.innerHTML = t.t_water_return >= 0 ? "+" + t.t_water_return : t.t_water_return,
                r = document.getElementById("Thot_water"),
                r.innerHTML = t.t_hot_water >= 0 ? "+" + t.t_hot_water : t.t_hot_water,
                r = document.getElementById("mode");
                let n = 255 & t.s
                  , o = t.s >> 16 & 15;
                r.innerHTML = ["СТОП", "НАГРЕВ", "ГВС", "ТЭН"][n] + ["", "", " + ГВС", "", " + ГВС"][o],
                r = document.getElementById("LastTime"),
                r.innerHTML = t.LastTime,
                r = document.getElementById("LastDate"),
                r.innerHTML = t.LastDate,
                t.flow_rates[0],
                r = document.getElementById("FlowRates"),
                r.innerHTML = t.flow_rates[0] + (t.dualmode ? "/" + t.flow_rates[1] : ""),
                r = document.getElementById("Pin"),
                r.innerHTML = t.t_Pinput / 10 + " кВт";
                let l = Math.round(1.25 * t.flow_rates[0] * 60 * (t.t_water_supply - t.t_water_return))
                  , a = t.dualmode ? Math.round(1.25 * t.flow_rates[1] * 60 * (t.t_water_supply2 - t.t_water_return2)) : 0;
                r = document.getElementById("Pout"),
                r.innerHTML = Math.round((l + a) / 100) / 10 + " кВт",
                r = document.getElementById("set_air"),
                r.innerHTML = t.set_air,
                r = document.getElementById("set_water"),
                r.innerHTML = t.set_water,
                r = document.getElementById("set_gvs"),
                r.innerHTML = t.set_gvs,
                r = document.getElementById("error"),
                r.hidden = !t.num_errors,
                r = document.getElementById("CompFreq");
                let s = t.power_comp >> 8 & 255
                  , d = t.power_comp >> 16 & 255;
                r.innerHTML = s + (t.dualmode ? "/" + d + " Гц" : " Гц"),
                r = document.getElementById("Tcond");
                let i = Math.round(t.t_freon_supply)
                  , c = i >= 0 ? "+" + i : i;
                i = Math.round(t.t_freon_supply2);
                let u = i >= 0 ? "+" + i : i;
                r.innerHTML = c + (t.dualmode ? "/" + u : "");
                let m = 15 & t.P
                  , p = 0;
                m < 6 && (p = 10 * m),
                6 == m && (p = 55),
                m > 6 && (p = 10 * (m - 1)),
                r = document.getElementById("scale"),
                r.style.height = p + "%",
                r = document.getElementById("percent"),
                r.innerHTML = p + "%"
            }
            function y(e) {
                if (!e.success)
                    return -1;
                if (0 != e.status) {
                    if (null == e.content || "" == e.content)
                        return;
                    if (2 == e.cmd)
                        document.getElementById("config_view").innerHTML = e.content;
                    else if (3 == e.cmd)
                        console.log(e.content);
                    else if (4 == e.cmd)
                        document.getElementById("schedule_view").innerHTML = e.content;
                    else if (5 == e.cmd)
                        console.log(e.content);
                    else if (7 == e.cmd) {
                        if (200 == e.status) {
                            let r = JSON.parse(e.content);
                            d.username = r.username,
                            d.password = r.password,
                            d.serial = r.serial,
                            d.keep_alive = !1,
                            null == d.theme && (d.theme = 0);
                            var t = JSON.stringify(d);
                            localStorage.setItem("temzit_app_settings", t),
                            u("username", d.username),
                            u("userpassword", d.password),
                            u("serial", d.serial),
                            u("token", 0),
                            window.location.href = s + "/app"
                        }
                    } else {
                        let t = JSON.parse(e.content);
                        if (clearInterval(n),
                        200 != e.status) {
                            let t = document.getElementById("mode");
                            switch (e.status) {
                            case 401:
                                t.innerHTML = "Ошибка авторизации";
                                break;
                            case 402:
                            case 403:
                                t.innerHTML = "Не правильный серийный номер";
                                break;
                            case 404:
                                t.innerHTML = "Нет данных";
                                break;
                            case 406:
                                t.innerHTML = "Нет доступа";
                                break;
                            case 500:
                                t.innerHTML = "Ошибка сервера";
                                break;
                            default:
                                t.innerHTML = "Ошибка связи"
                            }
                            c = !1,
                            _()
                        } else {
                            localStorage.setItem("temzit_app_server_data", e.content);
                            let r = t.keep_alive;
                            c = !0,
                            p(),
                            r ? (i = r,
                            console.log(i),
                            d.keep_alive && (n = setInterval("SendRequest()", 6e4))) : clearInterval(n)
                        }
                    }
                }
            }
            function g(e=1) {
                if (null == d.serial || null == d.username || null == d.password || "" == d.username || "" == d.password || "" == d.serial)
                    return document.getElementById("mode").innerHTML = "нажмите на красный кружок",
                    c = !1,
                    void _();
                if (0 == a & l < 255) {
                    l = 1;
                    var t = s;
                    t += "/api/GetData",
                    t += "?cmd=" + e,
                    d.keep_alive && (t += "&token=" + i),
                    "service.temzit.ru" != document.location.hostname && (t += "&login=" + d.username + "&serial=" + d.serial + "&pass=" + d.password),
                    m(t, y, e)
                }
            }
            function b() {
                let e = "white"
                  , t = "black"
                  , r = "#FFFFFF"
                  , n = "#000000"
                  , o = "none"
                  , l = "white";
                0 == d.theme ? (e = "black",
                t = "white",
                r = "#000000",
                n = "#FFFFFF",
                l = "black") : 2 == d.theme && (e = "black",
                t = "white",
                r = "#000000",
                n = "#000000",
                l = "#47C6D9",
                o = 'url("img/splash.png")');
                let a = document.querySelector("body");
                a.style.backgroundColor = e,
                a.style.color = t,
                a.style.backgroundImage = o,
                a = document.querySelector("html"),
                a.style.backgroundColor = e,
                a.style.color = t;
                const s = document.styleSheets[0];
                for (let e of s.cssRules)
                    ".svg_pic" == e.selectorText && (e.style.fill = n,
                    e.style.stroke = n),
                    ".settings_text" == e.selectorText && (e.style.color = t),
                    ".cfg_row select" == e.selectorText && (e.style.backgroundColor = l,
                    e.style.color = t),
                    ".schedule_row select" == e.selectorText && (e.style.backgroundColor = l,
                    e.style.color = t)
            }
            function w(e, t) {
                for (err_txt = "",
                k = 1; k <= 2; k++)
                    1 == k && (kkb_error = 65535 & e),
                    2 == k && (kkb_error = e >> 16 & 65535),
                    kkb_error > 255 && (kkb_error >>= 8),
                    254 == t || t >= 20 && t <= 24 ? (209 == kkb_error && (err_txt += "d1 Нет связи с модулем инвертора <br>"),
                    210 == kkb_error && (err_txt += "d2 Нет связи с модулем вентилятора <br>"),
                    211 == kkb_error && (err_txt += "d3 Нет связи с основным модулем <br>"),
                    177 == kkb_error && (err_txt += "b1 Сработал датчик высокое давление <br>"),
                    180 == kkb_error && (err_txt += "b4 Сработал датчик низкое давление <br>"),
                    59 == kkb_error && (err_txt += "3b Неисправность вентилятора  <br>"),
                    53 == kkb_error && (err_txt += "35 Авария инвертора, смотри код  <br>"),
                    193 == kkb_error && (err_txt += "с1 Не работает датчик Тулицы<br>"),
                    198 == kkb_error && (err_txt += "с6 Не работает датчик Твсасывания <br>"),
                    194 == kkb_error && (err_txt += "с2 Не работает датчик Тразморозки <br>"),
                    195 == kkb_error && (err_txt += "с3 Перегрев компрессора <br>"),
                    196 == kkb_error && (err_txt += "с4 Не работает датчик Тнагнетания <br>"),
                    188 == kkb_error && (err_txt += "bc Компрессор не раскрутился до фиксированной частоты <br>"),
                    189 == kkb_error && (err_txt += "bd Маленький ток компрессора, возможно не переключится 4х клапан <br>")) : t > 3 && t < 20 && (15 == (127 & kkb_error) && (err_txt += "код 15 - в ККБ не поступает сигнал от ГМ  <br>"),
                    43 == (127 & kkb_error) && (err_txt += "код 43 - Сработал датчик низкого давления <br>"),
                    44 == (127 & kkb_error) && (err_txt += "код 44 - Сработал датчик высого давления <br>")),
                    "" == err_txt && 0 != kkb_error && (err_txt += "код " + kkb_error + " Смотрите руководство на компрессорный блок <br>");
                return err_txt
            }
            window.ShowSettings = function(e) {
                let t;
                clearInterval(n);
                let r = localStorage.temzit_app_settings;
                null != r && (d = JSON.parse(r),
                t = document.getElementById("username"),
                t.value = d.username,
                t = document.getElementById("password"),
                t.value = d.password,
                t = document.getElementById("serial"),
                t.value = d.serial,
                t = document.getElementById("keep_alive"),
                t.checked = d.keep_alive,
                t = document.getElementById("theme"),
                t.selectedIndex = d.theme),
                t = document.getElementById("page_main"),
                t.style.display = "none",
                t = document.getElementById("page_settings"),
                t.style.display = ""
            }
            ,
            window.SaveSettings = function(e) {
                let t;
                if ("btn_ok" == e.id) {
                    t = document.getElementById("username"),
                    d.username = t.value,
                    t = document.getElementById("password"),
                    d.password = t.value,
                    t = document.getElementById("serial"),
                    d.serial = t.value,
                    t = document.getElementById("keep_alive"),
                    d.keep_alive = t.checked,
                    i = 0,
                    t = document.getElementById("theme"),
                    d.theme = t.selectedIndex;
                    var r = JSON.stringify(d);
                    localStorage.setItem("temzit_app_settings", r),
                    u("username", d.username),
                    u("userpassword", d.password),
                    u("serial", d.serial),
                    u("token", 0),
                    b()
                }
                t = document.getElementById("page_settings"),
                t.style.display = "none",
                t = document.getElementById("page_main"),
                t.style.display = "",
                g()
            }
            ,
            window.ShowPage = function(e, t, r) {
                let o;
                clearInterval(n),
                null != localStorage.temzit_app_settings && g(r),
                o = document.getElementById("page_main"),
                o.style.display = "none",
                o = document.getElementById(t),
                o.style.display = "",
                "page_stats" == t && DrawStats(),
                "page_errors" == t && function() {
                    let e = localStorage.temzit_app_server_data;
                    if (null == e || "" == e)
                        return;
                    let t = JSON.parse(e);
                    var r = t.num_errors
                      , n = t.kkb_error;
                    err_txt = "",
                    1 & r && (err_txt += "E05 Не включается контактор ТЭН <br>"),
                    2 & r && (err_txt += "E08 Нет связи с ККБ1 <br>"),
                    4 & r && (err_txt += "E01 Низкий проток в канале 1 <br>"),
                    8 & r && (err_txt += "E07 Нет связи с WiFi датчиком температуры <br>"),
                    16 & r && (err_txt += "E09 ККБ 1 не переключается в нагрев <br>"),
                    32 & r && (err_txt += "E02 Высокая температура фреона на газовой трубе канал 1 <br>"),
                    64 & r && (err_txt += "E03 Низкая температура фреона  на жидкостной трубе канал 1 <br>"),
                    128 & r && (err_txt += "E04 Авария ККБ1<br>",
                    err_txt += "&nbsp;&nbsp;" + w(65535 & n, t.kkb1_type)),
                    256 & r && (err_txt += "E06 Сбросились часы или расписание <br>"),
                    16384 & r && (err_txt += "E0F Критичная неисправность датчиа температуры, (красная колонка) <br>"),
                    32768 & r && (err_txt += "E0G Не верная прошивка дисплея <br>"),
                    131072 & r && (err_txt += "E18 Нет связи с ККБ2 <br>"),
                    262144 & r && (err_txt += "E11 Низкий проток в канале 2 <br>"),
                    1048576 & r && (err_txt += "E19 ККБ 2 не переключается в нагрев <br>"),
                    2097152 & r && (err_txt += "E12 Высокая температура фреона на газовой трубе канал 2 <br>"),
                    4194304 & r && (err_txt += "E13 Низкая температура фреона  на жидкостной трубе канал 2 <br>"),
                    8388608 & r && (err_txt += "E14 Авария ККБ2<br>",
                    err_txt += "&nbsp;&nbsp;" + w(4294901760 & n, t.kkb2_type)),
                    document.getElementById("errors_text").innerHTML = err_txt
                }()
            }
            ,
            window.ClosePage = function(e, t, r) {
                let n;
                "submit" == e.name ? g(r) : g(1),
                n = document.getElementById(t),
                n.style.display = "none",
                n = document.getElementById("page_main"),
                n.style.display = ""
            }
            ,
            window.getTop = function() {
                let e = localStorage.temzit_app_settings;
                null != e && (d = JSON.parse(e),
                b()),
                p(),
                g()
            }
            ,
            window.SetTheme = b,
            window.DrawStats = o.DrawStats,
            window.SendRequest = g,
            window.autologin = function() {
                var e = s;
                e += "/api/GetData",
                m(e += "?cmd=7", y, 7)
            }
        }
        ,
        368: (e,t,r)=>{
            r.r(t),
            r.d(t, {
                DrawStats: ()=>l
            });
            var n, o = r(730);
            function l() {
                var e = []
                  , t = [];
                $.plot("#placeholder", t, e);
                var r, l = null, a = null, s = null;
                function d() {
                    if (a = null,
                    l) {
                        var e = s
                          , t = n.getAxes();
                        if (!(e.x < t.xaxis.min || e.x > t.xaxis.max || e.y < t.yaxis.min || e.y > t.yaxis.max)) {
                            var r, o, d = n.getData();
                            for (r = 0; r < d.length; ++r) {
                                var i = d[r];
                                for (o = 0; o < i.data.length && !(i.data[o][0] > e.x); ++o)
                                    ;
                                var c, u = i.data[o - 1], m = i.data[o];
                                c = null == u ? m[1] : null == m ? u[1] : u[1] + (m[1] - u[1]) * (e.x - u[0]) / (m[0] - u[0]),
                                l.eq(r).text(i.label.replace(/=.*/, "=" + c.toFixed(2)))
                            }
                        }
                    }
                }
                r = o.l,
                r += "/api/GetData",
                r += "?cmd=6&login=" + o.X.username + "&serial=" + o.X.serial + "&pass=" + o.X.password,
                $.ajax({
                    url: r,
                    type: "GET",
                    dataType: "json",
                    success: function(r) {
                        null != r && (t = [r],
                        e = {
                            lines: {
                                show: !0
                            },
                            points: {
                                show: !1
                            },
                            xaxis: {
                                position: "bottom",
                                ticks: r.axis
                            },
                            crosshair: {
                                mode: "x"
                            },
                            grid: {
                                hoverable: !0,
                                autoHighlight: !1
                            }
                        },
                        n = $.plot("#placeholder", [{
                            data: r.t_outdoor,
                            label: "Tout=",
                            color: "orange"
                        }, {
                            data: r.t_indoor,
                            label: "Tin=",
                            color: "blue"
                        }, {
                            data: r.t_water_supply,
                            label: "Tws=",
                            color: "green"
                        }, {
                            data: r.t_water_return,
                            label: "Twr=",
                            color: "yellow"
                        }, {
                            data: r.t_freon_supply,
                            label: "Trs=",
                            color: "red"
                        }, {
                            data: r.t_freon_return,
                            label: "Trr=",
                            color: "pink"
                        }, {
                            data: r.t_hot_water,
                            label: "Thot=",
                            color: "lime"
                        }, {
                            data: r.t_hot_water2,
                            label: "Ttank=",
                            color: "grey"
                        }], e),
                        (l = $("#placeholder .legendLabel")).each((function() {
                            $(this).css("width", $(this).width())
                        }
                        )),
                        s && d())
                    }
                }),
                $("#placeholder").bind("plothover", (function(e, t, r) {
                    s = t,
                    a || (a = setTimeout(d, 50))
                }
                ))
            }
        }
    }
      , t = {};
    function r(n) {
        var o = t[n];
        if (void 0 !== o)
            return o.exports;
        var l = t[n] = {
            exports: {}
        };
        return e[n](l, l.exports, r),
        l.exports
    }
    r.d = (e,t)=>{
        for (var n in t)
            r.o(t, n) && !r.o(e, n) && Object.defineProperty(e, n, {
                enumerable: !0,
                get: t[n]
            })
    }
    ,
    r.o = (e,t)=>Object.prototype.hasOwnProperty.call(e, t),
    r.r = e=>{
        "undefined" != typeof Symbol && Symbol.toStringTag && Object.defineProperty(e, Symbol.toStringTag, {
            value: "Module"
        }),
        Object.defineProperty(e, "__esModule", {
            value: !0
        })
    }
    ,
    r(730)
}
)();