import random
from asyncio import sleep

import pandas as pd
import plotly.express as px

# from fasthtml.common import *
from fasthtml.common import (
    H3,
    Button,
    Div,
    # FastHTML,
    Form,
    Input,
    Label,
    P,
    Progress,
    Strong,
    Titled,
    fast_app,
    serve,
)
from fh_plotly import plotly2fasthtml, plotly_headers

# app = FastHTML(exts="ws", hdrs=(plotly_headers,))
# rt = app.route

app, rt = fast_app(hdrs=(plotly_headers,), exts="ws")



def generate_3d_scatter_chart():
    df = pd.DataFrame(
        {"x": [1, 2, 3, 4, 5, 6], "y": [7, 8, 6, 9, 7, 8], "z": [3, 5, 4, 6, 5, 7]}
    )
    fig = px.scatter_3d(df, x="x", y="y", z="z")
    return plotly2fasthtml(fig)


def get_progress(percent_complete: int):
    "Simulate progress check"
    return percent_complete + random.random() / 3


def update_progress(percent_complete: float):
    # Check if done
    if percent_complete >= 1:
        return H3("Job Complete!", id="progress_bar")
    # get progress
    # percent_complete = get_progress(percent_complete)
    # Update progress bar
    return progress_bar(percent_complete)


def progress_bar(percent_complete: float):
    return Progress(
        id="progress_bar",
        value=percent_complete,
        get=update_progress,
        hx_target="#progress_bar",
        hx_trigger="every 500ms",
        hx_vals=f"js:'percent_complete': '{percent_complete}'",
        hidden=False,
    )


@rt("/katapult/webapp")
def get():
    percent_complete = 0
    cts = Div(
        Form(
            #Label("msg", Input(id="msg", name="msg", value="msg")),
            Label("num", Input(id="num", name="num", value=5)),
            Label("wait", Input(id="wait", name="wait", value=1)),
            Button("Send", type="submit"),
            id="form",
            ws_send=True,
        ),
        Progress(
            id="progress_bar",
            value=percent_complete,
            hx_target="#progress_bar",
            hx_vals=f"js:'percent_complete': '{percent_complete}'",
            hidden=True,
        ),
        Div(id="notifications"),
        Div(id="plot"),
        hx_ext="ws",
        ws_connect="/katapult/webapp/ws",
    )
    return Titled("Example Webapp (uses websockets)", cts)


@app.ws("/katapult/webapp/ws")
async def ws(num: int, wait: int, send):
#async def ws(msg: str, num: int, wait: int, send):
    print(f"app.ws triggered with num={num}, wait={wait}")
    for i in range(num +1):
        await send(update_progress(i / num))
        # await send(Div(P(f"{i} / {num}"), hx_swap_oob="beforeend:#notifications"))
        await send(Div(P(f"{i} / {num}"), id="notifications"))
        await sleep(wait)
        print(f"sent {i}")
    plot = Div(
        Strong("3D Scatter Chart"),
        Div(generate_3d_scatter_chart()),
        id="plot",
    )
    await send(plot)


serve()
