require 'sinatra/base'
require 'opentelemetry/sdk'
require 'json'

$stdout.sync = true
$stderr.sync = true

# Use ConsoleSpanExporter to see ALL spans on stdout
OpenTelemetry::SDK.configure do |c|
  c.service_name = 'hasher-local-test'
  c.add_span_processor(
    OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(
      OpenTelemetry::SDK::Trace::Export::ConsoleSpanExporter.new
    )
  )
  c.use 'OpenTelemetry::Instrumentation::Sinatra'
end

puts "OTEL configured with ConsoleSpanExporter"

class TestApp < Sinatra::Base
  set :protection, false
  set :bind, '0.0.0.0'
  set :port, 4567

  get '/' do
    "OK\n"
  end

  get '/error' do
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.set_attribute('http.response.status_code', 500)
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    status 500
    "ERROR\n"
  end

  get '/slow' do
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('processing.time_seconds', 1.0)
    sleep 1
    "SLOW OK\n"
  end
end

TestApp.run!
