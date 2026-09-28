# Web and Docker

## Running in a browser

```bash
pip install ovos-tui-client[web]
ovos-tui --web
```

Open the printed URL in a browser. It is the same interface, running
on the server instead of in your terminal.

- `--web-port` sets the port (default `8000`).
- `--web-host` sets the address the server **binds to**. It is
  detected automatically if you leave it out; guessing wrong here shows
  a broken, unstyled page rather than a clear error.

### When the bind address isn't the address your browser uses

`--web-host` is also the address written into the page's own asset and
WebSocket URLs. Normally that's fine: the address the server binds to
is the one your browser reaches.

Behind Docker port publishing, NAT, or a Home Assistant OS add-on's
own network, it isn't. A container can bind `0.0.0.0` or its internal
address, but a browser can't reach `http://0.0.0.0:8000/`, and binding
the host's real LAN address fails with
`OSError: could not bind on any address`.

In that case add `--web-public-url`:

```bash
ovos-tui --web --web-host 0.0.0.0 --web-public-url http://192.168.1.50:8000
```

`--web-host` still decides the bind address; `--web-public-url` only
changes the address written into the page. Set it to whatever your
browser actually uses.

## Docker/Podman companion image

The image is an interactive terminal tool, not a background service,
so it needs a TTY:

```bash
docker run -it --rm --network host ghcr.io/andlo/ovos-tui-client:latest
```

`--network host` reaches a messagebus listening on the host's
`127.0.0.1:8181`. On an `ovos-docker` install you can instead join that
stack's compose network and point `--host` at the messagebus container.

The TUI's own options go after the image name:

```bash
docker run -it --rm --network host ghcr.io/andlo/ovos-tui-client:latest --lang da-dk
```

`--web` works here too; `--network host` is the reliable choice for it,
so the detected address is the host's real one.

**Tags.** Each release is tagged with its version (`:0.2.0`). `:latest`
follows the newest *final* release; pre-releases (`0.2.0a3`) only get
their version tag.

### Logs, services and the Docker socket

If the volumes `ovos_core` uses for config and logs are mounted into
this container at the same paths, the TUI finds everything as on a
normal install.

Log bridging and service detection need the Docker socket, because the
TUI runs `docker ps` and `docker logs -f`:

```bash
docker run -it --rm --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --user root \
  ghcr.io/andlo/ovos-tui-client:latest
```

!!! warning "The socket is a real security trade-off"
    Mounting the Docker socket gives the container control of the
    host's whole Docker daemon. That's why it is opt-in.

`--user root` is needed for the container's user to use the socket on
a standard `dockerd`. Matching the socket's group GID instead is the
narrower alternative. Under rootless Podman even `--user root` was not
enough in testing.

## Docker/Podman installs

The TUI runs on the host, not inside OVOS's containers, so a few things
differ on a Docker/Podman install of OVOS:

- **Logs without log files.** `ovos-docker`'s sample `mycroft.conf`
  logs to stdout, so there are no log files on the host. When the TUI
  finds none but detects Docker/Podman, it bridges each container's
  `docker logs -f` into a few familiar sources: every `ovos_skill_*`
  container goes to `skills`, `ovos_audio` to `audio`, and so on, with
  unknown containers in `other`. The bridges stop when you quit. If no
  `docker`/`podman` command is available, the TUI says so.
- **Services** are containers. The TUI detects this and shows the
  number of containers; restarting a container from the TUI is not
  supported yet.
- **Skills.** `skillmanager.list` only reports skills loaded in the
  process that answers it. On a one-container-per-skill install that is
  almost nothing, so the skills list and activate/deactivate are
  limited (issue #26).
- **Pipeline.** Pass `--mycroft-conf` (usually in
  `/home/ovos/ovos/config`, see `OVOS_CONFIG_FOLDER` in the install's
  `.env`), or the pipeline view may read the wrong file.
- **Tests** find their sentences on GitHub from the installed skill
  package. If the skills are only installed inside containers, use
  `--golden-dir` with local checkouts.
