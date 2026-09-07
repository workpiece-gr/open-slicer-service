# FDM Authority v2 — final service-image publication review

The published CP5 manufacturing toolchain is only one immutable authority boundary. The final Workpiece service image that contains the exact application code, profiles, Python environment, and production Authority entrypoint must be reviewed and later published separately.

This checkpoint prepares that **final service image** for review. It does not publish the service image, deploy it, enable production, create RatRig qualification evidence, change `/v1/project`, or remove human workshop review.

## Separate service-image lock

`fdm-service.lock.json` is the final execution-image authority lock.

Its current review state is intentionally:

```text
schema: workpiece-fdm-authority-service-lock-v1
status: unpublished
image: ghcr.io/workpiece-gr/fdm-authority-service
digest: null
source_commit: null
platform: linux/amd64
build_recipe: Dockerfile.authority
toolchain:
  image: ghcr.io/workpiece-gr/fdm-slicer-toolchain
  digest: sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

The service lock is deliberately **not copied into the image**. A published service image cannot contain a lock that names its own digest without creating a circular self-reference. Production must receive the separately retained service lock as an external exact file/configuration input.

A future published service lock must contain both:

- the exact registry `sha256:` digest of the reviewed final service image;
- the exact 40-character source commit from which that image was built.

Production runtime validation must require the supplied `WORKPIECE_FDM_AUTHORITY_RUNTIME_REF` to equal the lock's exact `image@sha256:<digest>` reference and the runtime `SOURCE_COMMIT_SHA` to equal the lock's published source commit. A local Docker image id or another repository with the same-looking digest syntax is not sufficient.

## Review build

`.github/workflows/cp5-service-image-review.yml` is read-only with respect to registries:

- `contents: read`;
- `packages: read`;
- no package-write permission;
- no `docker push`;
- no `--push`.

The workflow:

1. validates the unpublished service lock against the already-published toolchain lock;
2. pulls the exact private toolchain **by immutable digest**;
3. re-hashes the published toolchain manifest, package inventory, and Orca runtime against `fdm-toolchain.publication.json`;
4. builds `Dockerfile.authority` with that exact digest-pinned toolchain as `TOOLCHAIN_IMAGE`;
5. records the local service image id only as candidate evidence;
6. retains the exact installed Python package inventory from `pip freeze --all`;
7. hashes the `app/` and `profiles/` source trees and verifies the same tree hashes inside the built service image;
8. verifies the embedded `requirements.txt` and published toolchain lock bytes;
9. starts the separate production Authority entrypoint and proves it remains disabled/fail-closed while the service lock is unpublished;
10. emits a deterministic `fdm-authority-service-publication-review/1.0.0` packet;
11. uploads review evidence only.

The review packet records the exact source commit, intended service-image repository, source-specific review tag, platform, exact published toolchain base, service-lock hash, service recipe hash, requirements hash, application/profile tree hashes, local service image id, exact installed Python inventory hash, and explicit non-publication/non-production state.

## Important reproducibility boundary

`requirements.txt` pins the top-level Python packages, but transitive Python dependencies are resolved during the service-image build. Therefore the review packet retains the exact installed package inventory. A later approved publication attempt must rebuild the candidate and compare that inventory and the other reviewed source/runtime evidence **before** any registry write. If the environment has drifted, stop and re-review rather than publishing a different service image under the old approval.

As with the toolchain publication, a local Docker image id is not the registry manifest digest and must never be copied into the service lock.

## Publication approval boundary

**STOP before any service-image registry write.**

Publishing the final service image requires a separate explicit approval tied to the exact review candidate. That approval should identify at least:

- exact source commit;
- `ghcr.io/workpiece-gr/fdm-authority-service`;
- review tag from the retained packet;
- `linux/amd64`;
- exact digest-pinned toolchain base;
- exact service publication-review workflow run/evidence.

If any reviewed source/environment evidence changes, return to review and obtain approval for the new candidate.

## After a future approved service publication

A controlled one-time publication must rebuild the exact reviewed service candidate, reproduce the reviewed source/environment evidence before push, publish only the approved tag, independently resolve the registry digest, pull the service image back **by digest**, re-verify the retained evidence, confirm package visibility, retain a durable publication receipt, and then transition `fdm-service.lock.json` in a separate reviewed checkpoint to `status: published` with the exact service registry digest and source commit.

A service-image publication or lock transition still does **not** deploy the service, enable the production API, create physical qualification evidence, make an order eligible, or remove human review.

## Remaining production gates

Even after a future final service-image publication, Workpiece still needs genuine RatRig physical qualification evidence for each exact qualifying profile/request combination, controlled production Authority service configuration/deployment, website production-mode promotion and independent package re-verification, per-order mandatory human workshop review, and controlled physical acceptance.

Generic Ender remains unqualified.
