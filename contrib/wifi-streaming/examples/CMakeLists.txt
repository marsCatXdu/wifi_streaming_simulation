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

execute_process(
  COMMAND git rev-parse HEAD
  WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
  OUTPUT_VARIABLE wifi_streaming_project_commit
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
)
if(NOT wifi_streaming_project_commit)
  set(wifi_streaming_project_commit "unknown")
endif()
if(TARGET streaming-experiment)
  target_compile_definitions(
    streaming-experiment
    PRIVATE WIFI_STREAMING_PROJECT_COMMIT="${wifi_streaming_project_commit}"
  )
endif()
