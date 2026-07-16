build_lib_example(
  NAME streaming-experiment
  SOURCE_FILES streaming-experiment.cc
  LIBRARIES_TO_LINK
    ${libwifi-streaming}
    ${libapplications}
    ${libcsma}
    ${libinternet}
    ${libmobility}
    ${libspectrum}
    ${libwifi}
)
