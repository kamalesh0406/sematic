load("//bazel:pipeline.bzl", "sematic_pipeline")

sematic_py_lib(
    name = "pipeline",
    srcs = ["pipeline.py"],
    deps = [
        "//sematic/ee:ray",
        "//sematic:calculator",
        "//sematic/resolvers:resource_requirements",
        "//sematic/examples/mnist/pytorch:mnist_train_sematic_lib",
    ],
    pip_deps=[
        "ray",
        "torch",
        "torchvision",
    ],
)

sematic_pipeline(
    name = "__main__",
    dev = True,
    registry = "558717131297.dkr.ecr.us-west-2.amazonaws.com",
    repository = "sematic-dev",
    base="@sematic-worker-cuda//image",
    deps = [
        ":pipeline",
        "//sematic:init",
        requirement("debugpy"),
    ],
)
