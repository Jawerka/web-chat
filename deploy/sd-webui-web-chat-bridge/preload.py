"""CLI defaults for sd-webui-web-chat-bridge (loaded before webui arg parse)."""


def preload(parser):
    parser.add_argument(
        "--web-chat-url",
        type=str,
        default="http://192.168.88.44:8090",
        help="web-chat base URL used by sd-webui-web-chat-bridge to fetch gallery imports",
    )
