from setuptools import setup, find_packages

setup(
    name="stacksight",
    version="1.0.0",
    description="Python SDK for StackSight API - detect hiring signals and tech stacks in one line",
    long_description=open("README.md").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="StackSight",
    url="https://github.com/ngryn187/careers-scraper",
    packages=find_packages(),
    install_requires=["requests>=2.28.0"],
    python_requires=">=3.7",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="jobs hiring api tech-stack careers saas",
)
